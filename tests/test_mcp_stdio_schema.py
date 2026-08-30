"""Regressão do schema anunciado pelo transporte MCP stdio."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tests.route0_support import routing_arguments, write_route_config


def _send(process: subprocess.Popen[str], payload: dict[str, object]) -> None:
    assert process.stdin is not None
    process.stdin.write(json.dumps(payload) + "\n")
    process.stdin.flush()


def _receive(process: subprocess.Popen[str]) -> dict[str, object]:
    assert process.stdout is not None
    return json.loads(process.stdout.readline())


def test_simple_optional_types_and_numeric_timeout_roundtrip(tmp_path: Path) -> None:
    config_dir = write_route_config(tmp_path / "route-config", providers=("local",))
    process = subprocess.Popen(
        [sys.executable, "-m", "athena"],
        cwd=Path(__file__).resolve().parents[1],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "ATHENA_CONFIG_DIR": str(config_dir)},
    )
    try:
        _send(process, {"jsonrpc": "2.0", "id": "tools", "method": "tools/list"})
        tools_response = _receive(process)
        tools = {
            tool["name"]: tool
            for tool in tools_response["result"]["tools"]  # type: ignore[index]
        }
        assert set(tools) == {
            "run_combo",
            "ask_provider",
            "get_execution",
            "list_executions",
            "cancel_execution",
            "submit_task",
            "get_task",
        }
        for name in ("run_combo", "ask_provider"):
            properties = tools[name]["inputSchema"]["properties"]
            assert properties["overall_timeout_s"]["type"] == "number"
            assert properties["profile"]["type"] == "string"

        assert tools["submit_task"]["inputSchema"] == {
            "type": "object",
            "additionalProperties": False,
            "required": ["idempotency_key", "task"],
            "properties": {
                "idempotency_key": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 256,
                    "description": (
                        "Limite anunciado em caracteres; o runtime aplica o "
                        "limite autoritativo de 256 bytes UTF-8."
                    ),
                },
                "task": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["task_type", "input"],
                    "properties": {
                        "task_type": {
                            "type": "string",
                            "pattern": "^[a-z][a-z0-9_.-]{0,127}$",
                            "maxLength": 128,
                        },
                        "input": {
                            "type": "string",
                            "maxLength": 32768,
                            "description": (
                                "Limite anunciado em caracteres; o runtime "
                                "aplica o limite autoritativo de 32 KiB UTF-8."
                            ),
                        },
                        "project_ref": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 1024,
                            "description": (
                                "Limite anunciado em caracteres; o runtime "
                                "aplica o limite autoritativo de 1024 bytes UTF-8."
                            ),
                        },
                        "constraints": {"type": "object"},
                        "expected_output": {"type": "object"},
                        "priority": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 9,
                            "default": 5,
                        },
                    },
                },
            },
        }
        assert tools["get_task"]["inputSchema"] == {
            "type": "object",
            "additionalProperties": False,
            "required": ["task_handle"],
            "properties": {"task_handle": {"type": "string", "minLength": 1}},
        }

        _send(
            process,
            {
                "jsonrpc": "2.0",
                "id": "numeric-timeout",
                "method": "tools/call",
                "params": {
                    "name": "run_combo",
                    "arguments": {
                        **routing_arguments(),
                        "attempts": [
                            {
                                "provider": "local",
                                "command": [sys.executable, "-c", "pass"],
                                "cwd": str(tmp_path),
                            }
                        ],
                        "overall_timeout_s": 15.0,
                    },
                },
            },
        )
        call_response = _receive(process)
        error = call_response.get("error")
        if isinstance(error, dict):
            assert error.get("code") != -32602
            assert "must be a positive finite number" not in str(error.get("message"))
    finally:
        if process.stdin is not None:
            process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
