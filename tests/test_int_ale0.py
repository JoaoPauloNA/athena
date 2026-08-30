"""INT-ALE-0: contratos públicos mínimos para integração Aletheia."""

from __future__ import annotations

import sys
from pathlib import Path

from athena.bridge import LocalBridgeRunner, RunRequest, RunResult
from athena.execution import ExecutionDeadlines, ExecutionRecord, ExecutionState
from athena.lease import DirectoryLeaseManager


def test_run_result_exposes_timed_out_from_lifecycle(tmp_path: Path) -> None:
    execution = ExecutionRecord(
        "int-ale0",
        deadlines=ExecutionDeadlines(absolute_timeout_s=0.25),
    )
    result = LocalBridgeRunner().run(
        RunRequest((sys.executable, "-c", "import time; time.sleep(2)"), tmp_path),
        execution,
        DirectoryLeaseManager(),
    )

    assert isinstance(result, RunResult)
    assert result.state is ExecutionState.TIMED_OUT
    assert result.timed_out is True


def test_timed_out_preserves_partial_streams(tmp_path: Path) -> None:
    script = (
        "import sys, time\n"
        "print('partial-out', flush=True)\n"
        "print('partial-err', file=sys.stderr, flush=True)\n"
        "time.sleep(2)\n"
    )
    execution = ExecutionRecord(
        "int-ale0",
        deadlines=ExecutionDeadlines(absolute_timeout_s=0.25),
    )
    result = LocalBridgeRunner().run(
        RunRequest((sys.executable, "-c", script), tmp_path),
        execution,
        DirectoryLeaseManager(),
    )

    assert result.timed_out is True
    assert "partial-out" in result.stdout
    assert "partial-err" in result.stderr
