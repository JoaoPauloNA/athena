"""Regressão do schema anunciado pelo transporte MCP stdio."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _send(process: subprocess.Popen[str], payload: dict[str, object]) -> None:
    assert process.stdin is not None
    process.stdin.write(json.dumps(payload) + "\n")
    process.stdin.flush()


def _receive(process: subprocess.Popen[str]) -> dict[str, object]:
    assert process.stdout is not None
    return json.loads(process.stdout.readline())


def test_simple_optional_types_and_numeric_timeout_roundtrip(tmp_path: Path) -> None:
    process = subprocess.Popen(
        [sys.executable, "-m", "athena"],
        cwd=Path(__file__).resolve().parents[1],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _send(process, {"jsonrpc": "2.0", "id": "tools", "method": "tools/list"})
        tools_response = _receive(process)
        tools = {
            tool["name"]: tool
            for tool in tools_response["result"]["tools"]  # type: ignore[index]
        }
        for name in ("run_combo", "ask_provider"):
            properties = tools[name]["inputSchema"]["properties"]
            assert properties["overall_timeout_s"]["type"] == "number"
            assert properties["profile"]["type"] == "string"

        _send(
            process,
            {
                "jsonrpc": "2.0",
                "id": "numeric-timeout",
                "method": "tools/call",
                "params": {
                    "name": "run_combo",
                    "arguments": {
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

