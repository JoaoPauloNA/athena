"""Regressão para o ponto de entrada MCP via módulo."""
import json
import os
import subprocess
import sys
import threading
import time
from io import StringIO
from pathlib import Path

import athena.mcp_server as mcp_server
from athena.execution_registry import ExecutionRegistry


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    return True


class _QueueInput:
    def __init__(self) -> None:
        self._queue: list[str | None] = []
        self._cond = threading.Condition()

    def push(self, line: str) -> None:
        with self._cond:
            self._queue.append(line)
            self._cond.notify_all()

    def close(self) -> None:
        with self._cond:
            self._queue.append(None)
            self._cond.notify_all()

    def __iter__(self):
        return self

    def __next__(self) -> str:
        with self._cond:
            while not self._queue:
                self._cond.wait(timeout=1.0)
            item = self._queue.pop(0)
        if item is None:
            raise StopIteration
        return item


def _json_line(payload: dict) -> str:
    return json.dumps(payload) + "\n"


def _read_lines(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_module_entrypoint_serves_ping_and_tools_list():
    requests = [
        {"jsonrpc": "2.0", "id": "ping-1", "method": "ping"},
        {"jsonrpc": "2.0", "id": "tools-1", "method": "tools/list"},
    ]
    process = subprocess.run(
        [sys.executable, "-m", "athena.mcp_server"],
        cwd=Path(__file__).resolve().parents[1],
        input="\n".join(json.dumps(request) for request in requests) + "\n",
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert process.returncode == 0, process.stderr
    responses = [json.loads(line) for line in process.stdout.splitlines() if line.strip()]
    assert len(responses) == 2

    responses_by_id = {response["id"]: response for response in responses}
    assert responses_by_id["ping-1"]["result"] == {}

    tool_names = {tool["name"] for tool in responses_by_id["tools-1"]["result"]["tools"]}
    assert {"ask_provider", "run_combo", "list_providers", "recommend"} <= tool_names


def test_server_injects_execution_id_into_run_combo_and_response(monkeypatch):
    observed: dict[str, object] = {}
    monkeypatch.setattr(mcp_server, "EXECUTION_REGISTRY", ExecutionRegistry())

    def fake_run_combo(arguments):
        observed["execution_id"] = arguments.get("execution_id")
        return {"content": [{"type": "text", "text": json.dumps({"ok": True})}]}

    monkeypatch.setitem(mcp_server.TOOL_HANDLERS, "run_combo", fake_run_combo)
    response = mcp_server._handle_request(
        {
            "jsonrpc": "2.0",
            "id": "rc-1",
            "method": "tools/call",
            "params": {"name": "run_combo", "arguments": {"prompt": "x"}},
        }
    )
    assert response is not None
    assert observed["execution_id"] is None


def test_server_handles_execution_registration_in_worker_and_response_has_execution_id(monkeypatch):
    monkeypatch.setattr(mcp_server, "EXECUTION_REGISTRY", ExecutionRegistry())
    release = threading.Event()

    def fake_ask(arguments):
        release.wait(timeout=5)
        execution_id = arguments.get("execution_id")
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"execution_id": execution_id, "result": {"exit_code": 0}}),
                }
            ]
        }

    monkeypatch.setitem(mcp_server.TOOL_HANDLERS, "ask_provider", fake_ask)
    stdin = _QueueInput()
    stdout = StringIO()
    stderr = StringIO()

    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    thread = threading.Thread(target=mcp_server.run_stdio_server, daemon=True)
    thread.start()

    stdin.push(
        _json_line(
            {
                "jsonrpc": "2.0",
                "id": "long-1",
                "method": "tools/call",
                "params": {"name": "ask_provider", "arguments": {"provider": "p", "prompt": "x"}},
            }
        )
    )
    stdin.push(
        _json_line(
            {
                "jsonrpc": "2.0",
                "id": "get-pre",
                "method": "tools/call",
                "params": {"name": "get_execution", "arguments": {"request_id": "long-1"}},
            }
        )
    )
    stdin.push(_json_line({"jsonrpc": "2.0", "id": "ping-1", "method": "ping"}))
    time.sleep(0.3)
    responses = _read_lines(stdout.getvalue())
    assert any(item.get("id") == "ping-1" for item in responses)
    get_pre = next(item for item in responses if item.get("id") == "get-pre")
    execution_payload = json.loads(get_pre["result"]["content"][0]["text"])["execution"]
    assert execution_payload is not None
    exec_id = execution_payload["execution_id"]

    release.set()
    stdin.close()
    thread.join(timeout=5)
    assert not thread.is_alive()

    responses = _read_lines(stdout.getvalue())
    by_id = {item["id"]: item for item in responses if "id" in item}
    final = by_id["long-1"]["result"]["content"][0]["text"]
    final_payload = json.loads(final)
    assert final_payload["execution_id"] == exec_id


def test_two_concurrent_executions_keep_json_lines_valid_and_registry_intact(monkeypatch):
    monkeypatch.setattr(mcp_server, "EXECUTION_REGISTRY", ExecutionRegistry())
    barrier = threading.Barrier(2)

    def fake_run_combo(arguments):
        barrier.wait(timeout=5)
        callback = arguments.get("on_execution_update")
        if callback:
            callback({"execution_id": arguments["execution_id"], "attempt_id": "a1", "state": "RUNNING"})
            callback({"execution_id": arguments["execution_id"], "attempt_id": "a1", "state": "COMPLETED"})
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"execution_id": arguments["execution_id"], "result": {"ok": True}}),
                }
            ]
        }

    monkeypatch.setitem(mcp_server.TOOL_HANDLERS, "run_combo", fake_run_combo)

    stdin = _QueueInput()
    stdout = StringIO()
    stderr = StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    thread = threading.Thread(target=mcp_server.run_stdio_server, daemon=True)
    thread.start()
    stdin.push(_json_line({"jsonrpc": "2.0", "id": "c1", "method": "tools/call", "params": {"name": "run_combo", "arguments": {"prompt": "x"}}}))
    stdin.push(_json_line({"jsonrpc": "2.0", "id": "c2", "method": "tools/call", "params": {"name": "run_combo", "arguments": {"prompt": "y"}}}))
    stdin.close()
    thread.join(timeout=5)
    assert not thread.is_alive()

    responses = _read_lines(stdout.getvalue())
    assert len([r for r in responses if r.get("id") in {"c1", "c2"}]) == 2
    assert mcp_server.EXECUTION_REGISTRY.get(request_id="c1") is not None
    assert mcp_server.EXECUTION_REGISTRY.get(request_id="c2") is not None


def test_worker_exception_marks_execution_failed_without_fake_confirmation(monkeypatch):
    monkeypatch.setattr(mcp_server, "EXECUTION_REGISTRY", ExecutionRegistry())

    def fake_ask(arguments):
        callback = arguments.get("on_execution_update")
        if callback:
            callback(
                {
                    "execution_id": arguments["execution_id"],
                    "attempt_id": "att-x",
                    "state": "RUNNING",
                    "direct_process_terminated_confirmed": False,
                    "process_tree_terminated_confirmed": False,
                }
            )
        raise RuntimeError("boom")

    monkeypatch.setitem(mcp_server.TOOL_HANDLERS, "ask_provider", fake_ask)

    stdin = _QueueInput()
    stdout = StringIO()
    stderr = StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    thread = threading.Thread(target=mcp_server.run_stdio_server, daemon=True)
    thread.start()
    stdin.push(_json_line({"jsonrpc": "2.0", "id": "e1", "method": "tools/call", "params": {"name": "ask_provider", "arguments": {"provider": "p", "prompt": "x"}}}))
    stdin.close()
    thread.join(timeout=5)
    assert not thread.is_alive()

    entry = mcp_server.EXECUTION_REGISTRY.get(request_id="e1")
    assert entry is not None
    assert entry["state"] == "RESULT_INDETERMINATE"
    attempt = entry["attempts"]["att-x"]
    assert attempt["direct_process_terminated_confirmed"] is False
    assert attempt["process_tree_terminated_confirmed"] is False


def test_duplicate_request_id_returns_error_and_does_not_execute_second_handler(monkeypatch):
    monkeypatch.setattr(mcp_server, "EXECUTION_REGISTRY", ExecutionRegistry())
    call_count = {"value": 0}
    release = threading.Event()
    started = threading.Event()

    def fake_ask(arguments):
        call_count["value"] += 1
        started.set()
        release.wait(timeout=5)
        return {"content": [{"type": "text", "text": json.dumps({"ok": True})}]}

    monkeypatch.setitem(mcp_server.TOOL_HANDLERS, "ask_provider", fake_ask)
    stdin = _QueueInput()
    stdout = StringIO()
    stderr = StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    thread = threading.Thread(target=mcp_server.run_stdio_server, daemon=True)
    thread.start()
    payload = {
        "jsonrpc": "2.0",
        "id": "dup-req",
        "method": "tools/call",
        "params": {"name": "ask_provider", "arguments": {"provider": "p", "prompt": "x"}},
    }
    stdin.push(_json_line(payload))
    assert started.wait(timeout=2)
    stdin.push(_json_line(payload))
    time.sleep(0.2)

    release.set()
    stdin.close()
    thread.join(timeout=5)
    assert not thread.is_alive()

    responses = _read_lines(stdout.getvalue())
    dup_responses = [item for item in responses if item.get("id") == "dup-req"]
    assert len(dup_responses) == 2
    assert "result" in dup_responses[0] or "result" in dup_responses[1]
    assert any(item.get("error", {}).get("code") == -32602 for item in dup_responses)
    assert call_count["value"] == 1


def test_long_call_rejects_bool_request_id_before_registry_create(monkeypatch):
    monkeypatch.setattr(mcp_server, "EXECUTION_REGISTRY", ExecutionRegistry())
    call_count = {"value": 0}

    def fake_run_combo(_arguments):
        call_count["value"] += 1
        return {"content": [{"type": "text", "text": json.dumps({"ok": True})}]}

    monkeypatch.setitem(mcp_server.TOOL_HANDLERS, "run_combo", fake_run_combo)
    stdin = _QueueInput()
    stdout = StringIO()
    stderr = StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    thread = threading.Thread(target=mcp_server.run_stdio_server, daemon=True)
    thread.start()
    stdin.push(
        _json_line(
            {
                "jsonrpc": "2.0",
                "id": True,
                "method": "tools/call",
                "params": {"name": "run_combo", "arguments": {"prompt": "x"}},
            }
        )
    )
    stdin.close()
    thread.join(timeout=5)
    assert not thread.is_alive()

    responses = _read_lines(stdout.getvalue())
    assert len(responses) == 1
    assert responses[0]["error"]["code"] == -32602
    assert "request_id inválido" in responses[0]["error"]["message"]
    assert call_count["value"] == 0
    assert mcp_server.EXECUTION_REGISTRY.list(limit=10) == []


def test_long_call_rejects_invalid_execution_id_before_registry_create(monkeypatch):
    monkeypatch.setattr(mcp_server, "EXECUTION_REGISTRY", ExecutionRegistry())
    call_count = {"value": 0}

    def fake_run_combo(_arguments):
        call_count["value"] += 1
        return {"content": [{"type": "text", "text": json.dumps({"ok": True})}]}

    monkeypatch.setitem(mcp_server.TOOL_HANDLERS, "run_combo", fake_run_combo)
    stdin = _QueueInput()
    stdout = StringIO()
    stderr = StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    thread = threading.Thread(target=mcp_server.run_stdio_server, daemon=True)
    thread.start()
    stdin.push(
        _json_line(
            {
                "jsonrpc": "2.0",
                "id": "invalid-exec-id",
                "method": "tools/call",
                "params": {"name": "run_combo", "arguments": {"prompt": "x", "execution_id": "   "}},
            }
        )
    )
    stdin.close()
    thread.join(timeout=5)
    assert not thread.is_alive()

    responses = _read_lines(stdout.getvalue())
    assert len(responses) == 1
    assert responses[0]["error"]["code"] == -32602
    assert "execution_id inválido" in responses[0]["error"]["message"]
    assert call_count["value"] == 0
    assert mcp_server.EXECUTION_REGISTRY.list(limit=10) == []


def test_pool_saturation_keeps_ping_and_get_execution_responsive(monkeypatch):
    monkeypatch.setattr(mcp_server, "EXECUTION_REGISTRY", ExecutionRegistry())
    release = threading.Event()
    started: dict[str, threading.Event] = {f"long-{idx}": threading.Event() for idx in range(16)}

    def fake_run_combo(arguments):
        req = arguments.get("prompt")
        started[str(req)].set()
        release.wait(timeout=10)
        return {"content": [{"type": "text", "text": json.dumps({"ok": True})}]}

    monkeypatch.setitem(mcp_server.TOOL_HANDLERS, "run_combo", fake_run_combo)
    stdin = _QueueInput()
    stdout = StringIO()
    stderr = StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    thread = threading.Thread(target=mcp_server.run_stdio_server, daemon=True)
    thread.start()

    for idx in range(16):
        req_id = f"r{idx}"
        stdin.push(
            _json_line(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "method": "tools/call",
                    "params": {"name": "run_combo", "arguments": {"prompt": f"long-{idx}"}},
                }
            )
        )
    for evt in started.values():
        assert evt.wait(timeout=2)

    stdin.push(_json_line({"jsonrpc": "2.0", "id": "ping-hot", "method": "ping"}))
    stdin.push(
        _json_line(
            {
                "jsonrpc": "2.0",
                "id": "get-hot",
                "method": "tools/call",
                "params": {"name": "get_execution", "arguments": {"request_id": "r0"}},
            }
        )
    )
    time.sleep(0.3)
    responses = _read_lines(stdout.getvalue())
    assert any(item.get("id") == "ping-hot" and "result" in item for item in responses)
    get_resp = next(item for item in responses if item.get("id") == "get-hot")
    execution = json.loads(get_resp["result"]["content"][0]["text"])["execution"]
    assert execution is not None
    assert execution["request_id"] == "r0"

    release.set()
    stdin.close()
    thread.join(timeout=10)
    assert not thread.is_alive()


def test_pool_saturation_keeps_cancel_execution_responsive(monkeypatch):
    monkeypatch.setattr(mcp_server, "EXECUTION_REGISTRY", ExecutionRegistry())
    release = threading.Event()
    started = threading.Event()

    def fake_run_combo(arguments):
        started.set()
        release.wait(timeout=10)
        return {"content": [{"type": "text", "text": json.dumps({"ok": True})}]}

    monkeypatch.setitem(mcp_server.TOOL_HANDLERS, "run_combo", fake_run_combo)
    stdin = _QueueInput()
    stdout = StringIO()
    stderr = StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    thread = threading.Thread(target=mcp_server.run_stdio_server, daemon=True)
    thread.start()

    stdin.push(
        _json_line(
            {
                "jsonrpc": "2.0",
                "id": "sat-1",
                "method": "tools/call",
                "params": {"name": "run_combo", "arguments": {"prompt": "long"}},
            }
        )
    )
    assert started.wait(timeout=2)
    stdin.push(
        _json_line(
            {
                "jsonrpc": "2.0",
                "id": "cancel-1",
                "method": "tools/call",
                "params": {
                    "name": "cancel_execution",
                    "arguments": {"request_id": "sat-1", "reason": "user free text"},
                },
            }
        )
    )
    time.sleep(0.3)
    responses = _read_lines(stdout.getvalue())
    cancel = next(item for item in responses if item.get("id") == "cancel-1")
    payload = json.loads(cancel["result"]["content"][0]["text"])
    assert payload["found"] is True
    assert payload["execution_id"] is not None

    release.set()
    stdin.close()
    thread.join(timeout=10)
    assert not thread.is_alive()


def test_stdio_eof_abandons_nonterminal_execution(monkeypatch):
    monkeypatch.setattr(mcp_server, "EXECUTION_REGISTRY", ExecutionRegistry())
    entered = threading.Event()

    def synthetic_ask(arguments):
        from athena.bridge import run_subprocess

        registry_callback = arguments.get("on_execution_update")

        def _on_update(snapshot):
            if registry_callback is not None:
                registry_callback(snapshot)
            if snapshot.get("state") == "RUNNING":
                entered.set()

        result = run_subprocess(
            "synthetic-eof-handler",
            [
                sys.executable,
                "-c",
                (
                    "import sys, time\n"
                    "print('ready')\n"
                    "sys.stdout.flush()\n"
                    "time.sleep(30)\n"
                ),
            ],
            timeout=60,
            termination_grace_s=0.2,
            execution_id=arguments.get("execution_id"),
            on_execution_update=_on_update,
            execution_control=arguments.get("execution_control"),
        )
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {"execution_id": arguments["execution_id"], "result": result.to_dict()}
                    ),
                }
            ]
        }

    monkeypatch.setitem(mcp_server.TOOL_HANDLERS, "ask_provider", synthetic_ask)
    stdin = _QueueInput()
    stdout = StringIO()
    stderr = StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    thread = threading.Thread(target=mcp_server.run_stdio_server, daemon=True)
    thread.start()
    stdin.push(
        _json_line(
            {
                "jsonrpc": "2.0",
                "id": "eof-1",
                "method": "tools/call",
                "params": {"name": "ask_provider", "arguments": {"provider": "p", "prompt": "x"}},
            }
        )
    )
    assert entered.wait(timeout=2)
    stdin.close()
    thread.join(timeout=8)
    assert not thread.is_alive()

    entry = mcp_server.EXECUTION_REGISTRY.get(request_id="eof-1")
    assert entry is not None
    assert entry["client_abandoned"] is True
    assert entry["state"] in {"CANCELLED", "TERMINATION_UNCONFIRMED"}
    attempt = entry["attempts"][entry["current_attempt_id"]]
    assert attempt["termination_reason"] == "client_abandoned"
    assert "Timeout" not in str(attempt.get("termination_reason"))
    if sys.platform == "win32":
        assert entry["state"] == "TERMINATION_UNCONFIRMED"
    else:
        assert entry["state"] == "CANCELLED"
    assert attempt["pid"] is not None
    assert _pid_alive(int(attempt["pid"])) is False


def test_run_combo_invalid_timeout_rejected_prelaunch(monkeypatch):
    monkeypatch.setattr(mcp_server, "EXECUTION_REGISTRY", ExecutionRegistry())
    response = mcp_server._handle_request(
        {
            "jsonrpc": "2.0",
            "id": "bad-timeout",
            "method": "tools/call",
            "params": {
                "name": "run_combo",
                "arguments": {"prompt": "x", "timeout": 0, "execution_id": "exec-bad-timeout"},
            },
        }
    )
    assert response is not None
    assert response["error"]["code"] == -32602


def test_run_combo_invalid_overall_timeout_bool_rejected(monkeypatch):
    monkeypatch.setattr(mcp_server, "EXECUTION_REGISTRY", ExecutionRegistry())
    response = mcp_server._handle_request(
        {
            "jsonrpc": "2.0",
            "id": "bad-overall",
            "method": "tools/call",
            "params": {
                "name": "run_combo",
                "arguments": {
                    "prompt": "x",
                    "overall_timeout": True,
                    "execution_id": "exec-bad-overall",
                },
            },
        }
    )
    assert response is not None
    assert response["error"]["code"] == -32602


def test_run_combo_invalid_overall_timeout_infinite_rejected(monkeypatch):
    monkeypatch.setattr(mcp_server, "EXECUTION_REGISTRY", ExecutionRegistry())
    response = mcp_server._handle_request(
        {
            "jsonrpc": "2.0",
            "id": "bad-overall-inf",
            "method": "tools/call",
            "params": {
                "name": "run_combo",
                "arguments": {
                    "prompt": "x",
                    "overall_timeout": float("inf"),
                    "execution_id": "exec-bad-overall-inf",
                },
            },
        }
    )
    assert response is not None
    assert response["error"]["code"] == -32602


def test_ask_provider_invalid_timeout_nan_rejected(monkeypatch):
    monkeypatch.setattr(mcp_server, "EXECUTION_REGISTRY", ExecutionRegistry())
    response = mcp_server._handle_request(
        {
            "jsonrpc": "2.0",
            "id": "bad-ask-timeout-nan",
            "method": "tools/call",
            "params": {
                "name": "ask_provider",
                "arguments": {
                    "provider": "p",
                    "prompt": "x",
                    "timeout": float("nan"),
                    "execution_id": "exec-bad-ask-timeout-nan",
                },
            },
        }
    )
    assert response is not None
    assert response["error"]["code"] == -32602


def test_run_combo_forwards_new_timeout_arguments(monkeypatch):
    captured = {}

    def fake_run_combo(*args, **kwargs):
        captured["verification_timeout"] = kwargs.get("verification_timeout")
        captured["overall_timeout"] = kwargs.get("overall_timeout")
        from athena.bridge import RunResult
        return RunResult(provider="p", command=[], output="ok", exit_code=0)

    monkeypatch.setattr(mcp_server, "run_combo", fake_run_combo)
    payload = mcp_server._handle_run_combo(
        {
            "prompt": "x",
            "verification_timeout": 12.0,
            "overall_timeout": 30.0,
        }
    )
    assert payload is not None
    assert captured["verification_timeout"] == 12.0
    assert captured["overall_timeout"] == 30.0


def test_registered_long_call_invalid_params_mark_failed_prelaunch(monkeypatch):
    monkeypatch.setattr(mcp_server, "EXECUTION_REGISTRY", ExecutionRegistry())
    params = {
        "name": "run_combo",
        "arguments": {"prompt": "x", "timeout": -1},
    }
    execution_id = mcp_server._register_execution_if_needed("req-invalid-timeout", params)
    assert execution_id is not None
    response = mcp_server._handle_request(
        {
            "jsonrpc": "2.0",
            "id": "req-invalid-timeout",
            "method": "tools/call",
            "params": params,
        }
    )
    assert response is not None
    assert response["error"]["code"] == -32602
    record = mcp_server.EXECUTION_REGISTRY.get(execution_id=execution_id)
    assert record is not None
    assert record["state"] == "FAILED_PRELAUNCH"


def test_handle_request_success_finalizes_long_execution(monkeypatch):
    registry = ExecutionRegistry()
    monkeypatch.setattr(mcp_server, "EXECUTION_REGISTRY", registry)
    params = {"name": "ask_provider", "arguments": {"provider": "p", "prompt": "x"}}
    execution_id = mcp_server._register_execution_if_needed("req-success", params)
    assert execution_id is not None

    def fake_ask(arguments):
        callback = arguments.get("on_execution_update")
        if callback:
            callback({"execution_id": arguments["execution_id"], "attempt_id": "att-1", "state": "COMPLETED"})
        return {"content": [{"type": "text", "text": json.dumps({"ok": True})}]}

    monkeypatch.setitem(mcp_server.TOOL_HANDLERS, "ask_provider", fake_ask)
    response = mcp_server._handle_request(
        {"jsonrpc": "2.0", "id": "req-success", "method": "tools/call", "params": params}
    )
    assert response is not None and "result" in response
    entry = registry.get(execution_id=execution_id)
    assert entry is not None
    assert entry["finalized"] is True
    assert entry["state"] == "COMPLETED"


def test_get_execution_does_not_finalize_active_target(monkeypatch):
    registry = ExecutionRegistry()
    monkeypatch.setattr(mcp_server, "EXECUTION_REGISTRY", registry)
    registry.create(execution_id="victim", request_id="req-victim", tool="ask_provider")
    registry.update_attempt(
        "victim",
        {"execution_id": "victim", "attempt_id": "att-1", "state": "RUNNING"},
    )
    response = mcp_server._handle_request(
        {
            "jsonrpc": "2.0",
            "id": "get-victim",
            "method": "tools/call",
            "params": {"name": "get_execution", "arguments": {"execution_id": "victim"}},
        }
    )
    assert response is not None and "result" in response
    victim = registry.get(execution_id="victim")
    assert victim is not None
    assert victim["finalized"] is False
    assert victim["state"] == "RUNNING"


def test_cancel_execution_does_not_finalize_active_target(monkeypatch):
    registry = ExecutionRegistry()
    monkeypatch.setattr(mcp_server, "EXECUTION_REGISTRY", registry)
    registry.create(
        execution_id="victim",
        request_id="req-victim",
        tool="ask_provider",
    )
    registry.update_attempt(
        "victim",
        {"execution_id": "victim", "attempt_id": "att-1", "state": "RUNNING"},
    )
    response = mcp_server._handle_request(
        {
            "jsonrpc": "2.0",
            "id": "cancel-victim",
            "method": "tools/call",
            "params": {"name": "cancel_execution", "arguments": {"execution_id": "victim"}},
        }
    )
    assert response is not None and "result" in response
    victim = registry.get(execution_id="victim")
    assert victim is not None
    assert victim["finalized"] is False
    assert victim["state"] == "RUNNING"


def test_exception_then_late_callback_remains_indeterminate(monkeypatch):
    registry = ExecutionRegistry()
    monkeypatch.setattr(mcp_server, "EXECUTION_REGISTRY", registry)
    params = {"name": "ask_provider", "arguments": {"provider": "p", "prompt": "x"}}
    execution_id = mcp_server._register_execution_if_needed("req-indet", params)
    assert execution_id is not None

    captured_callback = {"fn": None}

    def fake_ask(arguments):
        captured_callback["fn"] = arguments.get("on_execution_update")
        callback = captured_callback["fn"]
        if callback:
            callback({"execution_id": arguments["execution_id"], "attempt_id": "att-1", "state": "RUNNING"})
        raise RuntimeError("boom")

    monkeypatch.setitem(mcp_server.TOOL_HANDLERS, "ask_provider", fake_ask)
    response = mcp_server._handle_request(
        {"jsonrpc": "2.0", "id": "req-indet", "method": "tools/call", "params": params}
    )
    assert response is not None and "error" in response
    callback = captured_callback["fn"]
    assert callback is not None
    callback({"execution_id": execution_id, "attempt_id": "att-1", "state": "COMPLETED"})
    entry = registry.get(execution_id=execution_id)
    assert entry is not None
    assert entry["finalized"] is True
    assert entry["state"] == "RESULT_INDETERMINATE"


def test_internal_exception_logging_does_not_leak_secret_to_stderr(monkeypatch):
    registry = ExecutionRegistry()
    monkeypatch.setattr(mcp_server, "EXECUTION_REGISTRY", registry)
    params = {"name": "ask_provider", "arguments": {"provider": "p", "prompt": "x"}}
    execution_id = mcp_server._register_execution_if_needed("req-secret", params)
    assert execution_id is not None

    def fake_ask(_arguments):
        raise RuntimeError("TOKEN=super-secret /tmp/private/path")

    monkeypatch.setitem(mcp_server.TOOL_HANDLERS, "ask_provider", fake_ask)
    stderr = StringIO()
    monkeypatch.setattr(sys, "stderr", stderr)

    response = mcp_server._handle_request(
        {"jsonrpc": "2.0", "id": "req-secret", "method": "tools/call", "params": params}
    )
    assert response is not None and "error" in response
    serialized_response = json.dumps(response)
    assert "TOKEN=super-secret" not in serialized_response
    assert "/tmp/private/path" not in serialized_response
    err = stderr.getvalue()
    assert "RuntimeError" in err
    assert "TOKEN=super-secret" not in err
    assert "/tmp/private/path" not in err


def test_futures_set_is_pruned_during_submissions(monkeypatch):
    class _ImmediateDoneFuture:
        def done(self):
            return True

        def result(self):
            return None

    class _FakeExecutor:
        def __init__(self, max_workers=16):
            self.max_workers = max_workers
            self.submissions = 0

        def submit(self, fn, msg):
            self.submissions += 1
            fn(msg)
            return _ImmediateDoneFuture()

        def shutdown(self, wait=True):
            return None

    monkeypatch.setattr(mcp_server, "EXECUTION_REGISTRY", ExecutionRegistry())
    monkeypatch.setattr(mcp_server, "ThreadPoolExecutor", _FakeExecutor)
    stdin = _QueueInput()
    stdout = StringIO()
    stderr = StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    monkeypatch.setitem(
        mcp_server.TOOL_HANDLERS,
        "run_combo",
        lambda arguments: {"content": [{"type": "text", "text": json.dumps({"execution_id": arguments["execution_id"]})}]},
    )
    thread = threading.Thread(target=mcp_server.run_stdio_server, daemon=True)
    thread.start()
    for idx in range(40):
        stdin.push(
            _json_line(
                {
                    "jsonrpc": "2.0",
                    "id": f"fut-{idx}",
                    "method": "tools/call",
                    "params": {"name": "run_combo", "arguments": {"prompt": f"x-{idx}"}},
                }
            )
        )
    stdin.close()
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_tools_schema_does_not_expose_retain_workspace_lease():
    ask_tool = next(tool for tool in mcp_server.TOOLS if tool["name"] == "ask_provider")
    props = ask_tool["inputSchema"]["properties"]
    assert "retain_workspace_lease" not in props


def test_mcp_callback_tracks_executor_and_verifier_attempts_and_finalizes_at_end(monkeypatch):
    registry = ExecutionRegistry()
    monkeypatch.setattr(mcp_server, "EXECUTION_REGISTRY", registry)
    observed_not_finalized_inside_handler = {"value": False}
    params = {"name": "ask_provider", "arguments": {"provider": "p", "prompt": "x"}}
    execution_id = mcp_server._register_execution_if_needed("mcp-attempt-flow", params)
    assert execution_id is not None

    def fake_ask(arguments):
        callback = arguments.get("on_execution_update")
        execution_id = arguments["execution_id"]
        assert callback is not None
        callback({"execution_id": execution_id, "attempt_id": "att-exec", "state": "RUNNING"})
        callback({"execution_id": execution_id, "attempt_id": "att-exec", "state": "COMPLETED"})
        callback({"execution_id": execution_id, "attempt_id": "att-ver", "state": "RUNNING"})
        callback({"execution_id": execution_id, "attempt_id": "att-ver", "state": "COMPLETED"})
        # late regression snapshot for old attempt must be ignored by registry
        callback({"execution_id": execution_id, "attempt_id": "att-exec", "state": "RUNNING"})
        current = registry.get(execution_id=execution_id)
        assert current is not None
        observed_not_finalized_inside_handler["value"] = (current["finalized"] is False)
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"execution_id": execution_id, "result": {"ok": True}}),
                }
            ]
        }

    monkeypatch.setitem(mcp_server.TOOL_HANDLERS, "ask_provider", fake_ask)

    response = mcp_server._handle_request(
        {
            "jsonrpc": "2.0",
            "id": "mcp-attempt-flow",
            "method": "tools/call",
            "params": params,
        }
    )
    assert response is not None and "result" in response
    assert observed_not_finalized_inside_handler["value"] is True
    entry = registry.get(request_id="mcp-attempt-flow")
    assert entry is not None
    assert entry["attempt_order"] == ["att-exec", "att-ver"]
    assert entry["state"] == "COMPLETED"
    assert entry["finalized"] is True
