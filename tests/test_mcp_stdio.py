"""Contrato do transporte stdio/JSON-RPC do núcleo modular."""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import threading
import time
from io import StringIO
from pathlib import Path

import pytest

from athena.bridge import RunResult
from athena.execution import CancellationToken, ExecutionState
from athena.mcp_server import MCPServer, MCPServerDependencies
from athena.mcp_stdio import (
    JsonRpcStdioServer,
    MCPApplication,
    MCPApplicationContract,
    StdioTransport,
)
from athena.profiles import resolve_service_profile
from athena.registry import ExecutionRegistry
from athena.router import AllAttemptsFailed
from tests.route0_support import routing_arguments, write_route_config


class QueueInput:
    def __init__(self) -> None:
        self._items: list[str | None] = []
        self._condition = threading.Condition()

    def push(self, payload: dict[str, object]) -> None:
        with self._condition:
            self._items.append(json.dumps(payload) + "\n")
            self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            self._items.append(None)
            self._condition.notify_all()

    def __iter__(self) -> QueueInput:
        return self

    def __next__(self) -> str:
        with self._condition:
            while not self._items:
                self._condition.wait()
            item = self._items.pop(0)
        if item is None:
            raise StopIteration
        return item


class BlockingRouter:
    def __init__(self, cwd: Path, *, fail: bool = False) -> None:
        self.cwd = cwd
        self.fail = fail
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def run(self, combo: object, *, control: object) -> RunResult:
        self.calls += 1
        self.started.set()
        while not self.release.wait(timeout=0.01):
            if control.cancellation_requested:  # type: ignore[attr-defined]
                break
        if self.fail:
            raise RuntimeError("private failure detail")
        state = (
            ExecutionState.CANCELLED
            if control.cancellation_requested  # type: ignore[attr-defined]
            else ExecutionState.COMPLETED
        )
        return RunResult(("synthetic",), self.cwd, state, 0, "ok", "", 0.01)


class NoResultFailureRouter:
    def run(self, combo: object, *, control: object) -> RunResult:
        raise AllAttemptsFailed("combo failed without a result")


def _server(
    tmp_path: Path, *, fail: bool = False, max_workers: int = 1
) -> tuple[JsonRpcStdioServer, QueueInput, StringIO, StringIO, ExecutionRegistry, BlockingRouter]:
    registry = ExecutionRegistry()
    router = BlockingRouter(tmp_path, fail=fail)
    core = MCPServer(
        MCPServerDependencies(
            router=router,
            registry=registry,
            verifier=lambda request, control: None,  # type: ignore[arg-type]
            profile_resolver=resolve_service_profile,
            control_factory=CancellationToken,
        )
    )
    application = MCPApplication(core)
    stdin = QueueInput()
    stdout = StringIO()
    stderr = StringIO()
    transport = StdioTransport(stdin, stdout, stderr)  # type: ignore[arg-type]
    return (
        JsonRpcStdioServer(application, transport, max_workers=max_workers),
        stdin,
        stdout,
        stderr,
        registry,
        router,
    )


def _call(request_id: object, name: str, arguments: object) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def _long_arguments(tmp_path: Path, execution_id: object = None) -> dict[str, object]:
    arguments: dict[str, object] = {
        "attempts": [
            {"provider": "synthetic", "command": ["synthetic"], "cwd": str(tmp_path)}
        ]
    }
    if execution_id is not None:
        arguments["execution_id"] = execution_id
    return arguments


def _responses(stdout: StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stdout.getvalue().splitlines() if line]


def _text_payload(response: dict[str, object]) -> dict[str, object]:
    result = response["result"]
    assert isinstance(result, dict)
    content = result["content"]
    assert isinstance(content, list)
    return json.loads(content[0]["text"])


def test_application_contract_and_exact_modular_tool_surface(tmp_path: Path) -> None:
    server, stdin, stdout, _, _, _ = _server(tmp_path)
    assert isinstance(server._application, MCPApplicationContract)
    thread = threading.Thread(target=server.serve)
    thread.start()
    stdin.push({"jsonrpc": "2.0", "id": "init", "method": "initialize"})
    stdin.push({"jsonrpc": "2.0", "method": "notifications/initialized"})
    stdin.push({"jsonrpc": "2.0", "id": "ping", "method": "ping"})
    stdin.push({"jsonrpc": "2.0", "id": "tools", "method": "tools/list"})
    stdin.close()
    thread.join(timeout=3)

    assert not thread.is_alive()
    by_id = {item["id"]: item for item in _responses(stdout)}
    assert by_id["init"]["result"]["protocolVersion"] == "2024-11-05"
    assert by_id["ping"]["result"] == {}
    assert [tool["name"] for tool in by_id["tools"]["result"]["tools"]] == [
        "run_combo",
        "ask_provider",
        "get_execution",
        "list_executions",
        "cancel_execution",
        "submit_task",
        "get_task",
    ]


def test_long_call_registers_early_and_control_calls_stay_responsive(
    tmp_path: Path,
) -> None:
    server, stdin, stdout, _, registry, router = _server(tmp_path)
    thread = threading.Thread(target=server.serve)
    thread.start()
    stdin.push(_call("long-1", "run_combo", _long_arguments(tmp_path)))
    assert router.started.wait(timeout=2)
    stdin.push(_call("get-1", "get_execution", {"request_id": "long-1"}))
    stdin.push({"jsonrpc": "2.0", "id": "ping-1", "method": "ping"})

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if {item.get("id") for item in _responses(stdout)} >= {"get-1", "ping-1"}:
            break
        time.sleep(0.01)
    by_id = {item["id"]: item for item in _responses(stdout)}
    execution = _text_payload(by_id["get-1"])["execution"]
    assert execution["state"] == "queued"
    assert registry.get(request_id="long-1") is not None
    assert by_id["ping-1"]["result"] == {}

    router.release.set()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if any(item.get("id") == "long-1" for item in _responses(stdout)):
            break
        time.sleep(0.01)
    stdin.close()
    thread.join(timeout=3)
    final = registry.get(request_id="long-1")
    assert final is not None and final["state"] == "completed" and final["finalized"]


def test_duplicate_and_invalid_long_ids_do_not_launch_handlers(tmp_path: Path) -> None:
    server, stdin, stdout, _, registry, router = _server(tmp_path)
    thread = threading.Thread(target=server.serve)
    thread.start()
    request = _call("duplicate", "run_combo", _long_arguments(tmp_path))
    stdin.push(request)
    assert router.started.wait(timeout=2)
    stdin.push(request)
    stdin.push(_call(True, "run_combo", _long_arguments(tmp_path)))
    stdin.push(_call("bad-execution", "run_combo", _long_arguments(tmp_path, "   ")))
    stdin.push(_call("bad-arguments", "run_combo", {}))
    router.release.set()
    stdin.close()
    thread.join(timeout=3)

    responses = _responses(stdout)
    duplicate = [item for item in responses if item.get("id") == "duplicate"]
    assert len(duplicate) == 2
    assert any(item.get("error", {}).get("code") == -32602 for item in duplicate)
    assert next(item for item in responses if item.get("id") is True)["error"]["code"] == -32602
    assert next(item for item in responses if item.get("id") == "bad-execution")["error"]["code"] == -32602
    assert next(item for item in responses if item.get("id") == "bad-arguments")["error"]["code"] == -32602
    assert router.calls == 1
    assert len(registry.list()) == 1


def test_concurrent_responses_are_complete_json_lines(tmp_path: Path) -> None:
    server, stdin, stdout, _, _, router = _server(tmp_path, max_workers=2)
    thread = threading.Thread(target=server.serve)
    thread.start()
    stdin.push(_call("one", "run_combo", _long_arguments(tmp_path, "exec-one")))
    stdin.push(_call("two", "run_combo", _long_arguments(tmp_path, "exec-two")))
    deadline = time.monotonic() + 2
    while router.calls < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    router.release.set()
    stdin.close()
    thread.join(timeout=3)

    lines = stdout.getvalue().splitlines()
    assert len(lines) == 2
    assert {json.loads(line)["id"] for line in lines} == {"one", "two"}


def test_worker_failure_finalizes_failed_without_leaking_detail(tmp_path: Path) -> None:
    server, stdin, stdout, stderr, registry, router = _server(tmp_path, fail=True)
    thread = threading.Thread(target=server.serve)
    thread.start()
    stdin.push(_call("failure", "run_combo", _long_arguments(tmp_path)))
    assert router.started.wait(timeout=2)
    router.release.set()
    stdin.close()
    thread.join(timeout=3)

    response = next(item for item in _responses(stdout) if item.get("id") == "failure")
    entry = registry.get(request_id="failure")
    assert response["error"]["code"] == -32000
    assert entry is not None and entry["state"] == "failed" and entry["finalized"]
    assert "private failure detail" not in stderr.getvalue()


def test_combo_failure_without_last_result_is_a_tool_error(tmp_path: Path) -> None:
    registry = ExecutionRegistry()
    core = MCPServer(
        MCPServerDependencies(
            router=NoResultFailureRouter(),  # type: ignore[arg-type]
            registry=registry,
            verifier=lambda request, control: None,  # type: ignore[arg-type]
            profile_resolver=resolve_service_profile,
            control_factory=CancellationToken,
        )
    )

    result = MCPApplication(core).call(
        "run_combo",
        _long_arguments(tmp_path, "no-result"),
        request_id="no-result-request",
    )

    assert result["isError"] is True
    content = result["content"]
    assert isinstance(content, list)
    payload = json.loads(content[0]["text"])
    assert payload == {
        "execution_id": "no-result",
        "result": {
            "state": None,
            "exit_code": None,
            "stdout": None,
            "stderr": None,
            "duration_s": None,
            "expired_deadline": None,
            "error": "combo failed without a result",
        },
    }


@pytest.mark.skipif(os.name != "posix", reason="process liveness requires POSIX")
def test_real_combo_timeout_returns_tool_error_and_leaves_no_orphan(
    tmp_path: Path,
) -> None:
    shell_pid_file = tmp_path / "shell.pid"
    child_pid_file = tmp_path / "child.pid"
    timeout_s = 3.0
    command = (
        'echo "$$" > "$1"; echo parcial; '
        '/bin/sleep 300 & echo "$!" > "$2"; wait'
    )
    request = _call(
        "real-timeout",
        "run_combo",
        {
            **routing_arguments(),
            "execution_id": "real-timeout-execution",
            "overall_timeout_s": timeout_s,
            "attempts": [
                {
                    "provider": "local",
                    "command": [
                        "/bin/sh",
                        "-c",
                        command,
                        "athena-timeout",
                        str(shell_pid_file),
                        str(child_pid_file),
                    ],
                    "cwd": str(tmp_path),
                    "termination_grace_s": 0.2,
                }
            ],
        },
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "athena"],
        cwd=Path(__file__).resolve().parents[1],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            **os.environ,
            "ATHENA_CONFIG_DIR": str(
                write_route_config(tmp_path / "route-config", providers=("local",))
            ),
        },
    )
    started = time.monotonic()
    try:
        assert process.stdin is not None
        assert process.stdout is not None
        process.stdin.write(json.dumps(request) + "\n")
        process.stdin.flush()
        readable, _, _ = select.select([process.stdout], [], [], timeout_s * 3)
        assert readable, "timed out waiting for the tool response"
        response = json.loads(process.stdout.readline())
        elapsed = time.monotonic() - started

        assert elapsed < timeout_s * 3
        assert "error" not in response
        tool_result = response["result"]
        assert tool_result["isError"] is True
        payload = json.loads(tool_result["content"][0]["text"])
        assert payload["execution_id"] == "real-timeout-execution"
        result = payload["result"]
        assert result["state"] == "timed_out"
        assert result["exit_code"] < 0
        assert "parcial" in result["stdout"]
        assert result["duration_s"] > 0
        assert result["expired_deadline"] == "absolute_deadline"

        pids = (int(shell_pid_file.read_text()), int(child_pid_file.read_text()))
        liveness_deadline = time.monotonic() + 2.0
        alive = set(pids)
        while alive and time.monotonic() < liveness_deadline:
            for pid in tuple(alive):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    alive.remove(pid)
            if alive:
                time.sleep(0.02)
        assert not alive, f"processes still alive after timeout: {sorted(alive)}"
    finally:
        if process.stdin is not None:
            process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def test_eof_abandons_execution_and_waits_for_worker(tmp_path: Path) -> None:
    server, stdin, _, _, registry, router = _server(tmp_path)
    thread = threading.Thread(target=server.serve)
    thread.start()
    stdin.push(_call("abandoned", "run_combo", _long_arguments(tmp_path)))
    assert router.started.wait(timeout=2)
    stdin.close()
    thread.join(timeout=3)

    assert not thread.is_alive()
    entry = registry.get(request_id="abandoned")
    assert entry is not None and entry["state"] == "cancelled" and entry["finalized"]


def test_module_entrypoint_serves_ping_and_exact_tools() -> None:
    requests = (
        {"jsonrpc": "2.0", "id": "ping", "method": "ping"},
        {"jsonrpc": "2.0", "id": "tools", "method": "tools/list"},
    )
    process = subprocess.run(
        [sys.executable, "-m", "athena"],
        cwd=Path(__file__).resolve().parents[1],
        input="".join(json.dumps(item) + "\n" for item in requests),
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert process.returncode == 0, process.stderr
    by_id = {item["id"]: item for item in map(json.loads, process.stdout.splitlines())}
    assert by_id["ping"]["result"] == {}
    assert {tool["name"] for tool in by_id["tools"]["result"]["tools"]} == {
        "run_combo",
        "ask_provider",
        "get_execution",
        "list_executions",
        "cancel_execution",
        "submit_task",
        "get_task",
    }
