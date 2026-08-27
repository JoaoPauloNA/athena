"""GATE 3 — EG-3A integração de produção: finalizador INJETADO na composição.

O MCPServer recebe o callable via MCPServerDependencies.artifact_finalizer;
não há import server→evidence_gate. O wrapper de produção vive na camada de
composição (mcp_runtime) e consome o motor EG-1.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

from athena.evidence_gate.pipeline_eg3a import FINAL_DELIVERY_STATUS

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))


def _make_finalizer():
    """Wrapper de composição (o mesmo que o runtime montará)."""
    from athena.evidence_gate.pipeline_eg3a import finalize_artifact

    def finalizer(envelope: dict) -> dict:
        return finalize_artifact(envelope, opt_in=True)
    return finalizer


def _server_with_finalizer(tmp_path, with_finalizer=True):
    if "test_mcp_server" not in sys.modules:
        sys.modules["test_mcp_server"] = importlib.import_module("test_mcp_server")
    tms = sys.modules["test_mcp_server"]
    from athena.mcp_server import MCPServer, MCPServerDependencies
    from athena.registry import ExecutionRegistry

    deps = MCPServerDependencies(
        router=tms.RecordingRouter(tms._result(tmp_path)),
        registry=ExecutionRegistry(),
        verifier=tms.RecordingVerifier(),
        profile_resolver=tms.resolve_service_profile,
        control_factory=tms.CancellationToken,
        artifact_finalizer=_make_finalizer() if with_finalizer else None,
    )
    return MCPServer(deps)


def _combo(tmp_path):
    tms = sys.modules["test_mcp_server"]
    return tms._combo(tmp_path)


def test_opt_in_off_sem_finalizador(tmp_path):
    server = _server_with_finalizer(tmp_path, with_finalizer=False)
    payload = server.run_combo(_combo(tmp_path), request_id="off")
    assert "evidence_gate" not in payload


def test_finalizador_injetado_produz_awaiting_human_review(tmp_path):
    server = _server_with_finalizer(tmp_path)
    payload = server.run_combo(_combo(tmp_path), request_id="on")
    eg = payload["evidence_gate"]
    assert eg["ran"] is True
    assert eg["delivery_status"] == "awaiting_human_review"
    assert {"execution_status", "validation_status", "delivery_status"} <= set(eg)


def test_finalizador_com_falha_sanitizada_nao_quebra(tmp_path):
    tms = sys.modules.get("test_mcp_server") or importlib.import_module(
        "test_mcp_server")
    from athena.mcp_server import MCPServer, MCPServerDependencies
    from athena.registry import ExecutionRegistry

    def broken(envelope):
        raise RuntimeError("falha interna com dados sensíveis fake-prompt")

    deps = MCPServerDependencies(
        router=tms.RecordingRouter(tms._result(tmp_path)),
        registry=ExecutionRegistry(),
        verifier=tms.RecordingVerifier(),
        profile_resolver=tms.resolve_service_profile,
        control_factory=tms.CancellationToken,
        artifact_finalizer=broken,
    )
    server = MCPServer(deps)
    payload = server.run_combo(_combo(tmp_path), request_id="broken")
    eg = payload["evidence_gate"]
    assert eg["ran"] is True
    assert "fake-prompt" not in json.dumps(eg)      # sanitizado
    assert eg["error"] == "finalization_error_sanitized"
    assert eg["validation_status"] == "escalate"    # falha → escalate, nunca PASS
    assert eg["delivery_status"] == "awaiting_human_review"
    assert payload["result"]["state"] == "completed"  # execução intacta


def test_tools_list_jsonrpc_cinco_tools(tmp_path):
    """tools/list via JSON-RPC real: exatamente 5 tools."""
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
    proc = subprocess.run(
        [sys.executable, "-m", "athena.mcp_runtime"],
        input="\n".join([
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        ]) + "\n",
        capture_output=True, text=True, timeout=30, cwd=str(REPO), env=env,
        check=False)
    assert proc.returncode == 0
    lines = [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]
    tools = next(m for m in lines if m.get("id") == 2)["result"]["tools"]
    assert sorted(t["name"] for t in tools) == [
        "ask_provider", "cancel_execution", "get_execution",
        "list_executions", "run_combo"]


def test_real_smoke_jsonrpc_eg3a_literal_marker(tmp_path):
    """Smoke real: run_combo com marcador literal via echo + evidence_gate."""
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""),
           "ATHENA_EG3A": "1"}
    combo = json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                        "params": {"name": "run_combo", "arguments": {
                            "attempts": [{"provider": "echo",
                                          "command": ["echo", "EG3A_MARKER_OK"],
                                          "cwd": str(tmp_path)}],
                            "overall_timeout_s": 15}}})
    # stdin aberto até a resposta: Popen + write + aguardar + close
    proc = subprocess.Popen(
        [sys.executable, "-m", "athena.mcp_runtime"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, cwd=str(REPO), env=env)
    try:
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1,
                                     "method": "initialize", "params": {}}) + "\n")
        proc.stdin.write(json.dumps({"jsonrpc": "2.0",
                                     "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()
        proc.stdin.write(combo + "\n")
        proc.stdin.flush()
        import time
        deadline = time.time() + 30
        lines: list[str] = []
        call = None
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            lines.append(line)
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == 3:
                call = msg
                break
        proc.stdin.close()
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert call is not None, f"run_combo sem resposta: {chr(10).join(lines[-3:])}"
    text = call["result"]["content"][0]["text"]
    assert "EG3A_MARKER_OK" in text  # marcador literal observado
    inner = json.loads(text)  # payload estruturado serializado no content
    assert "EG3A_MARKER_OK" in inner["result"]["stdout"]
    eg = inner["evidence_gate"]
    assert eg is not None, "evidence_gate ausente no payload de produção"
    assert eg["ran"] is True
    assert eg["delivery_status"] == "awaiting_human_review"


def test_eg4a_com_advisory_real_nunca_pass():
    """EG-4A com parecer real: FAIL/INCONCLUSIVE/ESCALATE não viram PASS."""
    from athena.evidence_gate.pipeline_eg3a import finalize_artifact

    bad = {"schema_version": "0.1", "task_id": "t", "attempt_id": "a",
           "declared_status": "completed", "claims": [], "checks": [],
           "artifacts": [], "telemetry": {"exit_code": 0}}
    r = finalize_artifact(bad, opt_in=True,
                          eg4_advisory="parecer do avaliador: aceitável")
    assert r["validation_status"] != "pass"
    assert r["eg4_advisory"].startswith("parecer")
    assert r["delivery_status"] == FINAL_DELIVERY_STATUS


def test_fail_inconclusive_escalate_nunca_pass():
    from athena.evidence_gate.pipeline_eg3a import finalize_artifact

    # claimed completed + evidência inconsistente (exit 1) → FAIL, nunca PASS
    env = {"schema_version": "0.1", "task_id": "t", "attempt_id": "a",
           "declared_status": "completed", "claims": [], "checks": [],
           "artifacts": [], "telemetry": {"exit_code": 1}}
    r = finalize_artifact(env, opt_in=True)
    assert r["validation_status"] != "pass"
    assert r["delivery_status"] == "awaiting_human_review"
