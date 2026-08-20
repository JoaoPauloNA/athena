"""Regressão determinística da integração do router com a fachada Aegis."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.regression
def test_compare_aegis_integration() -> None:
    """Comparar HEAD e baseline em processos e árvores de import isolados."""
    completed = subprocess.run(
        (sys.executable, "-m", "harness.compare_aegis_integration"),
        cwd=REPO_ROOT,
        check=False,
        text=True,
    )
    assert completed.returncode == 0
