"""Regressões lentas dos comparativos entre os núcleos novo e legado."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REGRESSION_ENABLED = os.environ.get("ATHENA_REGRESSION") == "1"
DIRECT_OLLAMA = Path("/usr/local/bin/ollama")
APP_OLLAMA = Path("/Applications/Ollama.app/Contents/Resources/ollama")


def _skip_reason(*required_binaries: Path) -> str | None:
    if not REGRESSION_ENABLED:
        return "defina ATHENA_REGRESSION=1 para habilitar a regressão lenta"
    missing = [str(path) for path in required_binaries if not path.is_file()]
    if missing:
        return "binário ollama ausente: " + ", ".join(missing)
    return None


def _run_harness(module: str, *args: str) -> None:
    completed = subprocess.run(
        (sys.executable, "-m", module, *args),
        cwd=REPO_ROOT,
        check=False,
        text=True,
    )
    assert completed.returncode == 0


@pytest.mark.regression
def test_compare_cores_deterministic() -> None:
    reason = _skip_reason(DIRECT_OLLAMA)
    if reason:
        pytest.skip(reason)
    _run_harness("harness.compare_cores_deterministic")


@pytest.mark.regression
def test_investigate_pty() -> None:
    reason = _skip_reason(DIRECT_OLLAMA, APP_OLLAMA)
    if reason:
        pytest.skip(reason)
    _run_harness("harness.investigate_pty", "--skip-gate")
