"""Testes da ponte local de subprocesso e PTY."""

from __future__ import annotations

import ast
import json
import os
import sys
import time
from pathlib import Path

import pytest

from athena.bridge import (
    BridgeRunnerContract,
    LocalBridgeRunner,
    RunRequest,
)
from athena.execution import (
    DeadlineKind,
    ExecutionDeadlines,
    ExecutionRecord,
    ExecutionState,
)
from athena.lease import DirectoryLeaseManager


def _record(
    *,
    deadlines: ExecutionDeadlines | None = None,
    updates: list[str] | None = None,
) -> ExecutionRecord:
    return ExecutionRecord(
        "bridge-test",
        deadlines=deadlines,
        on_update=(
            None if updates is None else lambda payload: updates.append(payload["state"])
        ),
    )


def _python(script: str, *arguments: str) -> tuple[str, ...]:
    return (sys.executable, "-c", script, *arguments)


def test_runner_satisfies_public_contract() -> None:
    assert isinstance(LocalBridgeRunner(), BridgeRunnerContract)


def test_subprocess_drives_complete_lifecycle_and_captures_streams(
    tmp_path: Path,
) -> None:
    updates: list[str] = []
    execution = _record(updates=updates)

    result = LocalBridgeRunner().run(
        RunRequest(
            _python(
                "import sys; print('out', flush=True); print('err', file=sys.stderr, flush=True)"
            ),
            tmp_path,
        ),
        execution,
        DirectoryLeaseManager(),
    )

    assert updates == ["starting", "running", "completed"]
    assert result.state is ExecutionState.COMPLETED
    assert result.exit_code == 0
    assert result.stdout == "out\n"
    assert result.stderr == "err\n"


def test_nonzero_exit_reaches_failed_terminal_state(tmp_path: Path) -> None:
    result = LocalBridgeRunner().run(
        RunRequest(_python("raise SystemExit(7)"), tmp_path),
        _record(),
        DirectoryLeaseManager(),
    )

    assert result.state is ExecutionState.FAILED
    assert result.exit_code == 7


def test_idle_deadline_expires_independently_during_silence(tmp_path: Path) -> None:
    execution = _record(
        deadlines=ExecutionDeadlines(absolute_timeout_s=2.0, idle_timeout_s=0.12)
    )

    result = LocalBridgeRunner().run(
        RunRequest(_python("import time; time.sleep(1)"), tmp_path),
        execution,
        DirectoryLeaseManager(),
    )

    assert result.state is ExecutionState.TIMED_OUT
    assert result.expired_deadline is DeadlineKind.IDLE
    assert result.duration_s < 1.0


def test_absolute_deadline_expires_despite_idle_progress(tmp_path: Path) -> None:
    execution = _record(
        deadlines=ExecutionDeadlines(absolute_timeout_s=0.3, idle_timeout_s=0.15)
    )
    script = (
        "import time\n"
        "for _ in range(20):\n"
        " print('progress', flush=True)\n"
        " time.sleep(0.04)\n"
    )

    result = LocalBridgeRunner().run(
        RunRequest(_python(script), tmp_path),
        execution,
        DirectoryLeaseManager(),
    )

    assert result.state is ExecutionState.TIMED_OUT
    assert result.expired_deadline is DeadlineKind.ABSOLUTE
    assert result.stdout.count("progress") >= 3


def test_pwd_matches_real_subprocess_cwd_even_if_caller_overrides_it(
    tmp_path: Path,
) -> None:
    script = "import json, os; print(json.dumps([os.getcwd(), os.environ['PWD']]))"

    result = LocalBridgeRunner().run(
        RunRequest(_python(script), tmp_path, env={"PWD": "/wrong/value"}),
        _record(),
        DirectoryLeaseManager(),
    )

    real_cwd, pwd = json.loads(result.stdout)
    assert Path(real_cwd) == tmp_path.resolve()
    assert Path(pwd) == tmp_path.resolve()


def test_workspace_lease_is_acquired_and_released(tmp_path: Path) -> None:
    class RecordingLease(DirectoryLeaseManager):
        def __init__(self) -> None:
            super().__init__()
            self.events: list[tuple[str, Path, str, str]] = []

        def acquire(
            self,
            directory: str | Path,
            execution_id: str,
            attempt_id: str,
            *,
            timeout: float | None = None,
        ) -> Path:
            key = super().acquire(
                directory, execution_id, attempt_id, timeout=timeout
            )
            self.events.append(("acquire", key, execution_id, attempt_id))
            return key

        def release(
            self, directory: str | Path, execution_id: str, attempt_id: str
        ) -> None:
            key = self.canonicalize(directory)
            self.events.append(("release", key, execution_id, attempt_id))
            super().release(directory, execution_id, attempt_id)

    lease = RecordingLease()
    execution = _record()

    LocalBridgeRunner().run(
        RunRequest(_python("print('ok')"), tmp_path), execution, lease
    )

    assert lease.events == [
        ("acquire", tmp_path.resolve(), execution.execution_id, execution.attempt_id),
        ("release", tmp_path.resolve(), execution.execution_id, execution.attempt_id),
    ]
    lease.acquire(tmp_path, "next-execution", "next-attempt", timeout=0)
    lease.release(tmp_path, "next-execution", "next-attempt")


@pytest.mark.skipif(os.name != "posix", reason="PTY requires POSIX")
def test_pty_runner_captures_output(tmp_path: Path) -> None:
    result = LocalBridgeRunner().run(
        RunRequest(_python("print('from-pty', flush=True)"), tmp_path, use_pty=True),
        _record(),
        DirectoryLeaseManager(),
    )

    assert result.state is ExecutionState.COMPLETED
    assert "from-pty" in result.stdout
    assert result.stderr == ""


@pytest.mark.skipif(os.name != "posix", reason="process groups require POSIX")
def test_live_descendant_escaped_from_group_blocks_positive_termination(
    tmp_path: Path,
) -> None:
    child_pid_file = tmp_path / "escaped.pid"
    script = (
        "import os, sys, time\n"
        "pid = os.fork()\n"
        "if pid == 0:\n"
        " os.setsid()\n"
        " open(sys.argv[1], 'w').write(str(os.getpid()))\n"
        " time.sleep(0.8)\n"
        " os._exit(0)\n"
        "while not os.path.exists(sys.argv[1]): time.sleep(0.01)\n"
        "time.sleep(0.2)\n"
    )

    result = LocalBridgeRunner().run(
        RunRequest(_python(script, str(child_pid_file)), tmp_path),
        _record(),
        DirectoryLeaseManager(),
    )

    assert result.state is ExecutionState.TERMINATION_UNCONFIRMED
    escaped_pid = int(child_pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        try:
            os.kill(escaped_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)


def test_bridge_imports_only_execution_and_lease_from_athena() -> None:
    package = Path(__file__).resolve().parents[1] / "athena" / "bridge"
    imported_core_packages: set[str] = set()

    for module in package.glob("*.py"):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported_core_packages.add(node.module)
            elif isinstance(node, ast.Import):
                imported_core_packages.update(alias.name for alias in node.names)

    assert {
        name.split(".")[1]
        for name in imported_core_packages
        if name.startswith("athena.")
    } <= {"execution", "lease"}
