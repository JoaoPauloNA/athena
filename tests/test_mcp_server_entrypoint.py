"""Regressão para o ponto de entrada MCP via módulo."""
import json
import subprocess
import sys
from pathlib import Path


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
