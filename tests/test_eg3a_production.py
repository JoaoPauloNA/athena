"""GATE 3 — EG-3A integração de produção por sink interno injetado.

O MCPServer recebe finalizador e sink por contrato, sem importar evidence_gate.
O wrapper concreto vive na composição (mcp_runtime) e consome o motor EG-1.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from copy import deepcopy
from dataclasses import replace
from io import StringIO
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


class RecordingSink:
    def __init__(self):
        self.deliveries = []

    def __call__(self, report, *, execution_id, tool):
        self.deliveries.append((deepcopy(report), execution_id, tool))


def _server_with_finalizer(tmp_path, with_finalizer=True, sink=None):
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
        artifact_sink=sink,
    )
    return MCPServer(deps)


def _combo(tmp_path):
    tms = sys.modules["test_mcp_server"]
    return tms._combo(tmp_path)


def test_opt_in_off_sem_finalizador_preserva_payload(tmp_path):
    server = _server_with_finalizer(tmp_path, with_finalizer=False)
    payload = server.run_combo(_combo(tmp_path), request_id="off")
    assert "evidence_gate" not in payload
    assert set(payload) == {"execution_id", "result"}


def test_finalizador_entrega_ao_sink_sem_alterar_payload(tmp_path):
    sink = RecordingSink()
    server = _server_with_finalizer(tmp_path, sink=sink)
    payload = server.run_combo(_combo(tmp_path), request_id="on")
    assert "evidence_gate" not in payload
    assert set(payload) == {"execution_id", "result"}
    assert len(sink.deliveries) == 1
    eg, execution_id, tool = sink.deliveries[0]
    assert eg["ran"] is True
    assert eg["delivery_status"] == "awaiting_human_review"
    assert {"execution_status", "validation_status", "delivery_status"} <= set(eg)
    assert execution_id == payload["execution_id"]
    assert tool == "run_combo"


def test_payloads_publicos_deep_equal_com_feature_off_e_on(tmp_path):
    combo = replace(_combo(tmp_path), execution_id="stable-execution")
    disabled = _server_with_finalizer(tmp_path, with_finalizer=False)
    sink = RecordingSink()
    enabled = _server_with_finalizer(tmp_path, sink=sink)

    run_disabled = disabled.run_combo(combo, request_id="run-off")
    run_enabled = enabled.run_combo(combo, request_id="run-on")
    assert run_enabled == run_disabled
    assert "evidence_gate" not in run_enabled

    ask_disabled = _server_with_finalizer(tmp_path, with_finalizer=False)
    ask_enabled = _server_with_finalizer(tmp_path, sink=sink)
    ask_off = ask_disabled.ask_provider(combo, request_id="ask-off")
    ask_on = ask_enabled.ask_provider(combo, request_id="ask-on")
    assert ask_on == ask_off
    assert "evidence_gate" not in ask_on


def test_finalizador_sem_sink_falha_fechado_e_preserva_payload(tmp_path):
    server = _server_with_finalizer(tmp_path, sink=None)
    payload = server.run_combo(_combo(tmp_path), request_id="no-sink")
    assert "evidence_gate" not in payload
    assert payload["result"]["state"] == "completed"


def test_runtime_habilitado_sem_diretorio_nao_configura_sink(monkeypatch):
    from athena.mcp_runtime import build_stdio_server
    from athena.mcp_stdio import StdioTransport

    monkeypatch.setenv("ATHENA_EG3A", "1")
    monkeypatch.delenv("ATHENA_EG3A_SINK_DIR", raising=False)
    runtime = build_stdio_server(
        StdioTransport(StringIO(), StringIO(), StringIO())
    )
    core = runtime._application._server
    assert core._artifact_finalizer is None
    assert core._artifact_sink is None


def test_finalizador_com_falha_sanitizada_nao_quebra(tmp_path):
    tms = sys.modules.get("test_mcp_server") or importlib.import_module(
        "test_mcp_server")
    from athena.mcp_server import MCPServer, MCPServerDependencies
    from athena.registry import ExecutionRegistry

    class BrokenSink:
        def __call__(self, report, *, execution_id, tool):
            raise RuntimeError("falha interna com dados sensíveis fake-prompt")

    deps = MCPServerDependencies(
        router=tms.RecordingRouter(tms._result(tmp_path)),
        registry=ExecutionRegistry(),
        verifier=tms.RecordingVerifier(),
        profile_resolver=tms.resolve_service_profile,
        control_factory=tms.CancellationToken,
        artifact_finalizer=_make_finalizer(),
        artifact_sink=BrokenSink(),
    )
    server = MCPServer(deps)
    payload = server.run_combo(_combo(tmp_path), request_id="broken")
    assert "evidence_gate" not in payload
    assert "fake-prompt" not in json.dumps(payload)
    assert payload["result"]["state"] == "completed"  # execução intacta
    assert server._artifact_delivery_failures == 1


def _tools_list(env):
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
    return next(m for m in lines if m.get("id") == 2)["result"]["tools"]


def test_tools_list_jsonrpc_cinco_tools_e_schemas_deep_equal(tmp_path):
    """Feature flag não muda tools/list nem qualquer schema existente."""
    base_env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
    }
    disabled_tools = _tools_list(base_env)
    enabled_tools = _tools_list({
        **base_env,
        "ATHENA_EG3A": "1",
        "ATHENA_EG3A_SINK_DIR": str(tmp_path / "sink"),
    })
    assert enabled_tools == disabled_tools
    assert sorted(t["name"] for t in disabled_tools) == [
        "ask_provider", "cancel_execution", "get_execution",
        "list_executions", "run_combo"]


def test_real_smoke_jsonrpc_eg3a_literal_marker(tmp_path):
    """Smoke real: marcador público intacto e relatório somente no sink."""
    sink_dir = tmp_path / "eg3a-sink"
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""),
           "ATHENA_EG3A": "1", "ATHENA_EG3A_SINK_DIR": str(sink_dir)}
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
    assert "evidence_gate" not in inner
    reports = list(sink_dir.glob("*.json"))
    assert len(reports) == 1
    stored = json.loads(reports[0].read_text())
    eg = stored["report"]
    assert eg["ran"] is True
    assert eg["delivery_status"] == "awaiting_human_review"
    assert stored["metadata"]["tool"] == "run_combo"


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
