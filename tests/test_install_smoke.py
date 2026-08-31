"""Clean-environment packaging smoke for the documented Athena install."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_AEGIS_ROOT = _REPO_ROOT.parent / "Aegis"
_EXPECTED_TOOLS = {
    "run_combo",
    "ask_provider",
    "get_execution",
    "list_executions",
    "cancel_execution",
    "submit_task",
    "get_task",
}


def _run(command: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=_REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
        env={**os.environ, "PIP_DISABLE_PIP_VERSION_CHECK": "1"},
    )


def test_readme_clean_venv_install_starts_athena_mcp(tmp_path: Path) -> None:
    """The README sequence installs Aegis and Athena into one fresh venv."""
    assert _AEGIS_ROOT.is_dir(), "clean-install smoke requires the sibling Aegis checkout"
    venv_dir = tmp_path / "venv"
    created = _run([sys.executable, "-m", "venv", str(venv_dir)])
    assert created.returncode == 0, created.stderr

    python = venv_dir / "bin" / "python"
    executable = venv_dir / "bin" / "athena-mcp"
    for package in (_AEGIS_ROOT, _REPO_ROOT):
        installed = _run(
            [str(python), "-m", "pip", "install", "-e", str(package)]
        )
        assert installed.returncode == 0, installed.stderr

    requests = (
        {"jsonrpc": "2.0", "id": "ping", "method": "ping"},
        {"jsonrpc": "2.0", "id": "tools", "method": "tools/list"},
    )
    process = subprocess.run(
        [str(executable)],
        input="".join(json.dumps(item) + "\n" for item in requests),
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
        env={**os.environ, "ATHENA_SKIP_AUTODISCOVERY": "1"},
    )
    assert process.returncode == 0, process.stderr
    responses = {item["id"]: item for item in map(json.loads, process.stdout.splitlines())}
    assert responses["ping"]["result"] == {}
    assert {tool["name"] for tool in responses["tools"]["result"]["tools"]} == _EXPECTED_TOOLS
