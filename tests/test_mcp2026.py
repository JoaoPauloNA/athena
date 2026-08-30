"""Focused tests for MCP protocol revision 2026-07-28 over stdio."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from io import StringIO
from pathlib import Path

import pytest

from athena.execution import CancellationToken
from athena.mcp_server import MCPServer, MCPServerDependencies
from athena.mcp_stdio import (
    MODERN_PROTOCOL_VERSION,
    SERVER_VERSION,
    JsonRpcStdioServer,
    MCPApplication,
    StdioTransport,
)
from athena.mcp_stdio.modern import (
    ModernMetaError,
    validate_modern_meta,
    wrap_modern_call_result,
)
from athena.profiles import resolve_service_profile
from athena.registry import ExecutionRegistry
from tests.test_mcp_stdio import BlockingRouter, QueueInput, _responses


def _modern_meta(*, version: str = MODERN_PROTOCOL_VERSION) -> dict[str, object]:
    return {
        "io.modelcontextprotocol/protocolVersion": version,
        "io.modelcontextprotocol/clientInfo": {"name": "probe", "version": "1.0.0"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }


def _server(
    tmp_path: Path, *, max_workers: int = 1
) -> tuple[JsonRpcStdioServer, QueueInput, StringIO, StringIO, BlockingRouter]:
    registry = ExecutionRegistry()
    router = BlockingRouter(tmp_path)
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
        router,
    )


class _InterleavingOutput(StringIO):
    """Make unlocked concurrent writes deterministically interleave."""

    def write(self, value: str) -> int:
        for character in value:
            super().write(character)
            time.sleep(0.00001)
        return len(value)


def test_modern_meta_validation_requires_mandatory_fields() -> None:
    with pytest.raises(ModernMetaError, match="missing protocolVersion"):
        validate_modern_meta({})
    with pytest.raises(ModernMetaError, match="missing clientCapabilities"):
        validate_modern_meta(
            {"io.modelcontextprotocol/protocolVersion": MODERN_PROTOCOL_VERSION}
        )


def test_modern_meta_accepts_missing_client_info() -> None:
    validate_modern_meta(
        {
            "io.modelcontextprotocol/protocolVersion": MODERN_PROTOCOL_VERSION,
            "io.modelcontextprotocol/clientCapabilities": {},
        }
    )


def test_modern_meta_rejects_malformed_client_info() -> None:
    with pytest.raises(ModernMetaError, match="clientInfo.name"):
        validate_modern_meta(
            {
                "io.modelcontextprotocol/protocolVersion": MODERN_PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientInfo": {"name": "", "version": "1"},
                "io.modelcontextprotocol/clientCapabilities": {},
            }
        )


def test_modern_meta_rejects_bad_protocol_version_format() -> None:
    with pytest.raises(ModernMetaError, match="YYYY-MM-DD"):
        validate_modern_meta(
            {
                "io.modelcontextprotocol/protocolVersion": "not-a-date",
                "io.modelcontextprotocol/clientInfo": {"name": "x", "version": "1"},
                "io.modelcontextprotocol/clientCapabilities": {},
            }
        )


def test_modern_meta_accepts_nested_capability_booleans() -> None:
    validate_modern_meta(
        {
            "io.modelcontextprotocol/protocolVersion": MODERN_PROTOCOL_VERSION,
            "io.modelcontextprotocol/clientInfo": {"name": "x", "version": "1"},
            "io.modelcontextprotocol/clientCapabilities": {
                "roots": {"listChanged": True},
                "experimental": {"count": 3, "tags": ["a"], "disabled": False},
            },
            "vendor.example/trace": "allowed",
        }
    )


def test_modern_meta_rejects_oversized_unknown_tree() -> None:
    huge = {"k": "x" * 300}
    with pytest.raises(ModernMetaError, match="string exceeds length"):
        validate_modern_meta(
            {
                "io.modelcontextprotocol/protocolVersion": MODERN_PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientInfo": {"name": "x", "version": "1"},
                "io.modelcontextprotocol/clientCapabilities": {},
                "extra": huge,
            }
        )


@pytest.mark.parametrize(
    "invalid_key",
    ["bad key", "1bad.example/value", "bad..example/value", "example/-bad", "a/b/c"],
)
def test_modern_meta_rejects_invalid_metaobject_key_syntax(invalid_key: str) -> None:
    meta = _modern_meta()
    meta[invalid_key] = "value"

    with pytest.raises(ModernMetaError, match="invalid syntax"):
        validate_modern_meta(meta)


def test_modern_wrapper_protects_reserved_result_fields() -> None:
    result = wrap_modern_call_result(
        {
            "resultType": "input_required",
            "_meta": {
                "io.modelcontextprotocol/serverInfo": {
                    "name": "attacker",
                    "version": "999",
                },
                "vendor.example/trace": "preserved",
            },
            "content": [],
        }
    )

    assert result["resultType"] == "complete"
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"] == {
        "name": "athena-mcp",
        "version": SERVER_VERSION,
    }
    assert result["_meta"]["vendor.example/trace"] == "preserved"


def test_server_discover_without_meta_is_unknown_method(tmp_path: Path) -> None:
    server, stdin, stdout, _, _ = _server(tmp_path)
    thread = threading.Thread(target=server.serve)
    thread.start()
    stdin.push({"jsonrpc": "2.0", "id": "discover", "method": "server/discover", "params": {}})
    stdin.close()
    thread.join(timeout=3)

    response = _responses(stdout)[0]
    assert response["error"]["code"] == -32601
    assert "server/discover" in str(response["error"]["message"])


def test_server_discover_with_partial_meta_is_invalid_params(tmp_path: Path) -> None:
    server, stdin, stdout, _, _ = _server(tmp_path)
    thread = threading.Thread(target=server.serve)
    thread.start()
    stdin.push(
        {
            "jsonrpc": "2.0",
            "id": "discover",
            "method": "server/discover",
            "params": {
                "_meta": {
                    "io.modelcontextprotocol/clientInfo": {"name": "x", "version": "1"},
                    "io.modelcontextprotocol/clientCapabilities": {},
                }
            },
        }
    )
    stdin.close()
    thread.join(timeout=3)

    response = _responses(stdout)[0]
    assert response["error"]["code"] == -32602


def test_partial_meta_on_tools_list_rejects_legacy_envelope(tmp_path: Path) -> None:
    server, stdin, stdout, _, _ = _server(tmp_path)
    thread = threading.Thread(target=server.serve)
    thread.start()
    stdin.push(
        {
            "jsonrpc": "2.0",
            "id": "list",
            "method": "tools/list",
            "params": {"_meta": {"io.modelcontextprotocol/clientCapabilities": {}}},
        }
    )
    stdin.close()
    thread.join(timeout=3)

    response = _responses(stdout)[0]
    assert response["error"]["code"] == -32602
    assert "result" not in response


def test_server_discover_returns_complete_envelope(tmp_path: Path) -> None:
    server, stdin, stdout, _, _ = _server(tmp_path)
    thread = threading.Thread(target=server.serve)
    thread.start()
    stdin.push(
        {
            "jsonrpc": "2.0",
            "id": "discover",
            "method": "server/discover",
            "params": {"_meta": _modern_meta()},
        }
    )
    stdin.close()
    thread.join(timeout=3)

    response = _responses(stdout)[0]
    result = response["result"]
    assert result["resultType"] == "complete"
    assert result["supportedVersions"] == [MODERN_PROTOCOL_VERSION]
    assert result["capabilities"] == {"tools": {}}
    assert "resources" not in result["capabilities"]
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["version"] == SERVER_VERSION


def test_unsupported_modern_version_returns_32022(tmp_path: Path) -> None:
    server, stdin, stdout, _, _ = _server(tmp_path)
    thread = threading.Thread(target=server.serve)
    thread.start()
    stdin.push(
        {
            "jsonrpc": "2.0",
            "id": "bad-version",
            "method": "tools/list",
            "params": {"_meta": _modern_meta(version="1900-01-01")},
        }
    )
    stdin.close()
    thread.join(timeout=3)

    response = _responses(stdout)[0]
    error = response["error"]
    assert error["code"] == -32022
    assert error["data"]["supported"] == [MODERN_PROTOCOL_VERSION]
    assert error["data"]["requested"] == "1900-01-01"


def test_modern_tools_list_and_call_include_result_type(tmp_path: Path) -> None:
    server, stdin, stdout, _, _router = _server(tmp_path)
    thread = threading.Thread(target=server.serve)
    thread.start()
    stdin.push(
        {
            "jsonrpc": "2.0",
            "id": "list",
            "method": "tools/list",
            "params": {"_meta": _modern_meta()},
        }
    )
    stdin.push(
        {
            "jsonrpc": "2.0",
            "id": "get",
            "method": "tools/call",
            "params": {
                "_meta": _modern_meta(),
                "name": "list_executions",
                "arguments": {"limit": 1},
            },
        }
    )
    stdin.close()
    thread.join(timeout=3)

    by_id = {item["id"]: item for item in _responses(stdout)}
    list_result = by_id["list"]["result"]
    assert list_result["resultType"] == "complete"
    assert [tool["name"] for tool in list_result["tools"]] == [
        "run_combo",
        "ask_provider",
        "get_execution",
        "list_executions",
        "cancel_execution",
        "submit_task",
        "get_task",
    ]
    call_result = by_id["get"]["result"]
    assert call_result["resultType"] == "complete"
    assert "executions" in json.loads(call_result["content"][0]["text"])


def test_legacy_initialize_and_tools_remain_unchanged(tmp_path: Path) -> None:
    server, stdin, stdout, _, _ = _server(tmp_path)
    thread = threading.Thread(target=server.serve)
    thread.start()
    stdin.push({"jsonrpc": "2.0", "id": "init", "method": "initialize", "params": {}})
    stdin.push({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
    stdin.push({"jsonrpc": "2.0", "id": "tools", "method": "tools/list", "params": {}})
    stdin.close()
    thread.join(timeout=3)

    by_id = {item["id"]: item for item in _responses(stdout)}
    init = by_id["init"]["result"]
    assert init["protocolVersion"] == "2024-11-05"
    assert init["serverInfo"]["version"] == SERVER_VERSION
    tools = by_id["tools"]["result"]["tools"]
    assert "resultType" not in by_id["tools"]["result"]
    assert len(tools) == 7


def test_initialize_with_modern_meta_is_rejected(tmp_path: Path) -> None:
    server, stdin, stdout, _, _ = _server(tmp_path)
    thread = threading.Thread(target=server.serve)
    thread.start()
    stdin.push(
        {
            "jsonrpc": "2.0",
            "id": "ambiguous",
            "method": "initialize",
            "params": {"_meta": _modern_meta()},
        }
    )
    stdin.close()
    thread.join(timeout=3)

    response = _responses(stdout)[0]
    assert response["error"]["code"] == -32600


def test_modern_cancellation_suppresses_later_response(tmp_path: Path) -> None:
    server, stdin, stdout, _, router = _server(tmp_path)
    thread = threading.Thread(target=server.serve)
    thread.start()
    stdin.push(
        {
            "jsonrpc": "2.0",
            "id": "long",
            "method": "tools/call",
            "params": {
                "_meta": _modern_meta(),
                "name": "run_combo",
                "arguments": {
                    "attempts": [
                        {
                            "provider": "synthetic",
                            "command": ["synthetic"],
                            "cwd": str(tmp_path),
                        }
                    ]
                },
            },
        }
    )
    assert router.started.wait(timeout=2)
    stdin.push(
        {
            "jsonrpc": "2.0",
            "method": "notifications/cancelled",
            "params": {"requestId": "long", "reason": "probe"},
        }
    )
    stdin.push({"jsonrpc": "2.0", "id": "ping-after-cancel", "method": "ping"})
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if any(item.get("id") == "ping-after-cancel" for item in _responses(stdout)):
            break
        time.sleep(0.01)
    router.release.set()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if any(item.get("id") == "long" for item in _responses(stdout)):
            break
        time.sleep(0.01)
    stdin.close()
    thread.join(timeout=3)

    assert not any(item.get("id") == "long" for item in _responses(stdout))


def test_unsupported_modern_run_combo_does_not_mutate_registry(tmp_path: Path) -> None:
    server, stdin, stdout, _, _router = _server(tmp_path)
    thread = threading.Thread(target=server.serve)
    thread.start()
    stdin.push(
        {
            "jsonrpc": "2.0",
            "id": "bad-long",
            "method": "tools/call",
            "params": {
                "_meta": _modern_meta(version="2099-01-01"),
                "name": "run_combo",
                "arguments": {
                    "attempts": [
                        {
                            "provider": "synthetic",
                            "command": ["synthetic"],
                            "cwd": str(tmp_path),
                        }
                    ]
                },
            },
        }
    )
    stdin.push(
        {
            "jsonrpc": "2.0",
            "id": "list-after-bad",
            "method": "tools/call",
            "params": {
                "_meta": _modern_meta(),
                "name": "list_executions",
                "arguments": {},
            },
        }
    )
    stdin.close()
    thread.join(timeout=3)

    by_id = {item["id"]: item for item in _responses(stdout)}
    assert by_id["bad-long"]["error"]["code"] == -32022
    list_payload = json.loads(by_id["list-after-bad"]["result"]["content"][0]["text"])
    assert list_payload["executions"] == []


def test_modern_ping_includes_result_type_and_server_info(tmp_path: Path) -> None:
    server, stdin, stdout, _, _ = _server(tmp_path)
    thread = threading.Thread(target=server.serve)
    thread.start()
    stdin.push(
        {
            "jsonrpc": "2.0",
            "id": "ping",
            "method": "ping",
            "params": {"_meta": _modern_meta()},
        }
    )
    stdin.close()
    thread.join(timeout=3)

    result = _responses(stdout)[0]["result"]
    assert result["resultType"] == "complete"
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["version"] == SERVER_VERSION


def test_oversized_input_line_returns_parse_error_without_side_effect(tmp_path: Path) -> None:
    from athena.mcp_stdio.modern import MAX_INPUT_LINE_BYTES

    server, stdin, stdout, _, _router = _server(tmp_path)
    thread = threading.Thread(target=server.serve)
    thread.start()
    oversized = " " + ("x" * (MAX_INPUT_LINE_BYTES + 1))
    with stdin._condition:
        stdin._items.append(oversized + "\n")
        stdin._condition.notify_all()
    stdin.push(
        {
            "jsonrpc": "2.0",
            "id": "after-big",
            "method": "ping",
        }
    )
    stdin.close()
    thread.join(timeout=3)

    response = _responses(stdout)[0]
    assert response["error"]["code"] == -32700
    assert _responses(stdout)[1]["id"] == "after-big"


def test_request_method_without_id_has_no_response_or_side_effect(tmp_path: Path) -> None:
    server, stdin, stdout, _, router = _server(tmp_path)
    thread = threading.Thread(target=server.serve)
    thread.start()
    stdin.push(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "run_combo",
                "arguments": {
                    "attempts": [
                        {
                            "provider": "synthetic",
                            "command": ["synthetic"],
                            "cwd": str(tmp_path),
                        }
                    ]
                },
            },
        }
    )
    stdin.push({"jsonrpc": "2.0", "id": "ping", "method": "ping"})
    stdin.close()
    thread.join(timeout=3)

    assert router.calls == 0
    assert len(_responses(stdout)) == 1
    assert _responses(stdout)[0]["id"] == "ping"


def test_cancel_notification_with_request_id_is_ignored(tmp_path: Path) -> None:
    server, stdin, stdout, _, router = _server(tmp_path)
    thread = threading.Thread(target=server.serve)
    thread.start()
    stdin.push(
        {
            "jsonrpc": "2.0",
            "id": "long",
            "method": "tools/call",
            "params": {
                "_meta": _modern_meta(),
                "name": "run_combo",
                "arguments": {
                    "attempts": [
                        {
                            "provider": "synthetic",
                            "command": ["synthetic"],
                            "cwd": str(tmp_path),
                        }
                    ]
                },
            },
        }
    )
    assert router.started.wait(timeout=2)
    stdin.push(
        {
            "jsonrpc": "2.0",
            "id": "bad-cancel",
            "method": "notifications/cancelled",
            "params": {"requestId": "long"},
        }
    )
    router.release.set()
    stdin.close()
    thread.join(timeout=3)

    assert any(item.get("id") == "long" for item in _responses(stdout))


def test_malformed_cancel_notification_does_not_crash_server(tmp_path: Path) -> None:
    server, stdin, stdout, stderr, _router = _server(tmp_path)
    thread = threading.Thread(target=server.serve)
    thread.start()
    for payload in (
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": "bad"},
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": True}},
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": []}},
        {
            "jsonrpc": "2.0",
            "method": "notifications/cancelled",
            "params": {"requestId": "missing", "reason": "x" * 512},
        },
    ):
        stdin.push(payload)
    stdin.push({"jsonrpc": "2.0", "id": "ping", "method": "ping"})
    stdin.close()
    thread.join(timeout=3)

    assert _responses(stdout)[0]["id"] == "ping"
    assert "Traceback" not in stderr.getvalue()


def test_cancel_before_request_does_not_block_reused_request_id(tmp_path: Path) -> None:
    server, stdin, stdout, _, _router = _server(tmp_path)
    thread = threading.Thread(target=server.serve)
    thread.start()
    stdin.push(
        {
            "jsonrpc": "2.0",
            "method": "notifications/cancelled",
            "params": {"requestId": "reuse", "reason": "early"},
        }
    )
    stdin.push(
        {
            "jsonrpc": "2.0",
            "id": "reuse",
            "method": "tools/call",
            "params": {
                "_meta": _modern_meta(),
                "name": "list_executions",
                "arguments": {"limit": 1},
            },
        }
    )
    stdin.close()
    thread.join(timeout=3)

    response = _responses(stdout)[0]
    assert response["id"] == "reuse"
    assert response["result"]["resultType"] == "complete"


def test_cancellation_state_is_cleaned_after_terminal_handling(tmp_path: Path) -> None:
    server, stdin, stdout, _, router = _server(tmp_path)
    thread = threading.Thread(target=server.serve)
    thread.start()
    stdin.push(
        {
            "jsonrpc": "2.0",
            "id": "long",
            "method": "tools/call",
            "params": {
                "_meta": _modern_meta(),
                "name": "run_combo",
                "arguments": {
                    "attempts": [
                        {
                            "provider": "synthetic",
                            "command": ["synthetic"],
                            "cwd": str(tmp_path),
                        }
                    ]
                },
            },
        }
    )
    assert router.started.wait(timeout=2)
    stdin.push(
        {
            "jsonrpc": "2.0",
            "method": "notifications/cancelled",
            "params": {"requestId": "long"},
        }
    )
    stdin.push({"jsonrpc": "2.0", "id": "ping-after-cancel", "method": "ping"})
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if any(item.get("id") == "ping-after-cancel" for item in _responses(stdout)):
            break
        time.sleep(0.01)
    router.release.set()
    stdin.close()
    thread.join(timeout=3)

    stdin2 = QueueInput()
    stdout2 = StringIO()
    transport2 = StdioTransport(stdin2, stdout2, StringIO())  # type: ignore[arg-type]
    server2 = JsonRpcStdioServer(server._application, transport2, max_workers=1)
    thread2 = threading.Thread(target=server2.serve)
    thread2.start()
    stdin2.push(
        {
            "jsonrpc": "2.0",
            "id": "long",
            "method": "tools/call",
            "params": {
                "_meta": _modern_meta(),
                "name": "list_executions",
                "arguments": {"limit": 1},
            },
        }
    )
    stdin2.close()
    thread2.join(timeout=3)

    reused = _responses(stdout2)[0]
    assert reused["id"] == "long"
    assert reused["result"]["resultType"] == "complete"


def test_cancellation_race_suppresses_response_under_lock(tmp_path: Path) -> None:
    server, stdin, stdout, _, router = _server(tmp_path)
    thread = threading.Thread(target=server.serve)
    thread.start()
    stdin.push(
        {
            "jsonrpc": "2.0",
            "id": "race",
            "method": "tools/call",
            "params": {
                "_meta": _modern_meta(),
                "name": "run_combo",
                "arguments": {
                    "attempts": [
                        {
                            "provider": "synthetic",
                            "command": ["synthetic"],
                            "cwd": str(tmp_path),
                        }
                    ]
                },
            },
        }
    )
    assert router.started.wait(timeout=2)
    for _ in range(20):
        stdin.push(
            {
                "jsonrpc": "2.0",
                "method": "notifications/cancelled",
                "params": {"requestId": "race", "reason": "burst"},
            }
        )
    stdin.push({"jsonrpc": "2.0", "id": "sync", "method": "ping"})
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if any(item.get("id") == "sync" for item in _responses(stdout)):
            break
        time.sleep(0.01)
    router.release.set()
    stdin.close()
    thread.join(timeout=3)

    ids = {item.get("id") for item in _responses(stdout)}
    assert "sync" in ids
    assert "race" not in ids


def test_concurrent_response_writes_remain_complete_json_lines(tmp_path: Path) -> None:
    base_server, _, _, _, _ = _server(tmp_path)
    stdout = _InterleavingOutput()
    transport = StdioTransport(StringIO(), stdout, StringIO())  # type: ignore[arg-type]
    server = JsonRpcStdioServer(base_server._application, transport, max_workers=1)
    count = 30
    with server._long_lock:
        server._inflight_long.update(f"long-{index}" for index in range(count))
    barrier = threading.Barrier(2)

    def finish_long_responses() -> None:
        barrier.wait()
        for index in range(count):
            server._finish_long_response(
                f"long-{index}",
                {"jsonrpc": "2.0", "id": f"long-{index}", "result": {}},
            )

    def write_regular_responses() -> None:
        barrier.wait()
        for index in range(count):
            server._write(
                {"jsonrpc": "2.0", "id": f"sync-{index}", "result": {}}
            )

    threads = [
        threading.Thread(target=finish_long_responses),
        threading.Thread(target=write_regular_responses),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    lines = stdout.getvalue().splitlines()
    assert len(lines) == count * 2
    decoded = [json.loads(line) for line in lines]
    assert len({item["id"] for item in decoded}) == count * 2


def test_duplicate_long_id_does_not_prepare_reserve_or_call_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server, stdin, stdout, _, router = _server(tmp_path, max_workers=2)
    real_prepare = server._application.prepare_long_call
    prepare_calls = 0

    def counted_prepare(name, arguments, request_id):
        nonlocal prepare_calls
        prepare_calls += 1
        return real_prepare(name, arguments, request_id)

    monkeypatch.setattr(server._application, "prepare_long_call", counted_prepare)
    request = {
        "jsonrpc": "2.0",
        "id": "duplicate",
        "method": "tools/call",
        "params": {
            "_meta": _modern_meta(),
            "name": "run_combo",
            "arguments": {
                "attempts": [
                    {
                        "provider": "synthetic",
                        "command": ["synthetic"],
                        "cwd": str(tmp_path),
                    }
                ]
            },
        },
    }
    thread = threading.Thread(target=server.serve)
    thread.start()
    stdin.push(request)
    assert router.started.wait(timeout=2)
    stdin.push(request)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if any("error" in item for item in _responses(stdout)):
            break
        time.sleep(0.01)
    router.release.set()
    stdin.close()
    thread.join(timeout=3)

    responses = _responses(stdout)
    assert prepare_calls == 1
    assert router.calls == 1
    assert len(responses) == 2
    assert sum("result" in item for item in responses) == 1
    duplicate_error = next(item["error"] for item in responses if "error" in item)
    assert duplicate_error == {
        "code": -32602,
        "message": "duplicate in-flight request id",
    }


def _spawn_athena() -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-m", "athena"],
        cwd=Path(__file__).resolve().parents[1],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )


def _send(process: subprocess.Popen[str], payload: dict[str, object]) -> None:
    assert process.stdin is not None
    process.stdin.write(json.dumps(payload) + "\n")
    process.stdin.flush()


def _receive(process: subprocess.Popen[str]) -> dict[str, object]:
    assert process.stdout is not None
    line = process.stdout.readline()
    assert line
    return json.loads(line)


def test_real_stdio_discover_and_legacy_flow() -> None:
    process = _spawn_athena()
    try:
        _send(
            process,
            {
                "jsonrpc": "2.0",
                "id": "discover",
                "method": "server/discover",
                "params": {"_meta": _modern_meta()},
            },
        )
        discover = _receive(process)
        assert discover["result"]["resultType"] == "complete"

        _send(process, {"jsonrpc": "2.0", "id": "init", "method": "initialize", "params": {}})
        init = _receive(process)
        assert init["result"]["serverInfo"]["version"] == SERVER_VERSION
        _send(process, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        _send(process, {"jsonrpc": "2.0", "id": "tools", "method": "tools/list", "params": {}})
        tools = _receive(process)
        assert len(tools["result"]["tools"]) == 7
    finally:
        if process.stdin is not None:
            process.stdin.close()
        process.wait(timeout=5)


def test_real_stdio_unsupported_modern_version() -> None:
    process = _spawn_athena()
    try:
        _send(
            process,
            {
                "jsonrpc": "2.0",
                "id": "bad",
                "method": "tools/list",
                "params": {"_meta": _modern_meta(version="2099-01-01")},
            },
        )
        response = _receive(process)
        assert response["error"]["code"] == -32022
    finally:
        if process.stdin is not None:
            process.stdin.close()
        process.wait(timeout=5)
