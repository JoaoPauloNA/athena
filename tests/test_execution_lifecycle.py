"""Synthetic process-lifecycle regression tests for the provider subprocess
transport (athena.bridge.run_subprocess).

All child/grandchild processes here are throwaway `python -c` scripts from
the standard library only — no real AI CLIs, network, or credentials are
invoked. Every test tracks the PIDs it creates and force-kills any survivor
in a `finally` block, on both success and failure.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time

import pytest

from athena.bridge import HAS_PTY, run_subprocess, run_with_pty
from athena.execution import (
    TERMINAL_STATES,
    DeadlineBudget,
    ExecutionControl,
    ExecutionRecord,
    ExecutionState,
    InvalidTransitionError,
)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False
    return True


def _force_kill(pid: int, timeout: float = 5.0) -> None:
    """Best-effort SIGKILL, tolerant of the pid already being gone.

    The target may be an orphaned grandchild that isn't our own child (it
    was reparented to init after its immediate parent was killed), so we
    cannot waitpid() on it — poll liveness instead until init reaps it.
    """
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and _pid_alive(pid):
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass
        time.sleep(0.05)


def _wait_for_pidfile(path: str, timeout: float = 5.0) -> int:
    deadline = time.monotonic() + timeout
    while not os.path.exists(path) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert os.path.exists(path), f"pidfile not found: {path}"
    with open(path) as f:
        payload = json.load(f)
    return int(payload["child_pid"])


def _same_pgid_parent_script(pidfile: str, child_exit: int = 0, parent_exit: int = 0) -> str:
    return (
        "import json, os, subprocess, sys, time\n"
        f"child = subprocess.Popen([sys.executable, '-c', \"import sys, time; time.sleep(30); sys.exit({child_exit})\"],"
        " stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)\n"
        f"with open({pidfile!r}, 'w') as f:\n"
        "    json.dump({'child_pid': child.pid, 'parent_pgid': os.getpgid(0)}, f)\n"
        "    f.flush()\n"
        "sys.exit(" + str(parent_exit) + ")\n"
    )


def test_child_that_refuses_to_die_is_reclaimed_after_timeout():
    """Parent (the direct child of run_subprocess) spawns a grandchild that
    ignores SIGTERM and detaches, then blocks well past a short timeout.

    run_subprocess() must still return promptly with timed_out=True (its
    SIGKILL on the direct child cannot be ignored, unlike SIGTERM). The
    orphaned grandchild is expected to survive the parent's death — that is
    documented lifecycle behavior, not something this test tries to change —
    so the test explicitly discovers and reaps it afterward.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        pidfile = os.path.join(tmpdir, "grandchild.pid")

        grandchild_script = (
            "import os, signal, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "os.setsid()\n"
            "time.sleep(30)\n"
        )
        parent_script = (
            "import subprocess, sys, time\n"
            f"gc = subprocess.Popen([sys.executable, '-c', {grandchild_script!r}])\n"
            f"with open({pidfile!r}, 'w') as f:\n"
            "    f.write(str(gc.pid))\n"
            "    f.flush()\n"
            "time.sleep(30)\n"
        )

        grandchild_pid: int | None = None
        try:
            result = run_subprocess(
                "synthetic-stubborn-child",
                [sys.executable, "-c", parent_script],
                timeout=1,
            )

            assert result.timed_out is True
            assert result.exit_code == 124
            assert result.duration_s < 10.0

            # The parent had time to fork+write the pidfile before being
            # killed (it writes immediately, well inside the 1s budget).
            deadline = time.monotonic() + 5.0
            while not os.path.exists(pidfile) and time.monotonic() < deadline:
                time.sleep(0.05)
            assert os.path.exists(pidfile), "grandchild never reported its pid"

            with open(pidfile) as f:
                grandchild_pid = int(f.read().strip())

            # Documents current behavior: run_subprocess only reaps the
            # direct child; a detached grandchild that ignores SIGTERM
            # outlives it.
            assert _pid_alive(grandchild_pid) is True
        finally:
            if grandchild_pid is not None:
                _force_kill(grandchild_pid)
                assert _pid_alive(grandchild_pid) is False


@pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX-only in this contract")
def test_natural_zero_exit_with_same_pgid_child_triggers_cleanup_and_completes():
    with tempfile.TemporaryDirectory() as tmpdir:
        pidfile = os.path.join(tmpdir, "child.json")
        child_pid: int | None = None
        try:
            result = run_subprocess(
                "synthetic-natural-zero-child",
                [sys.executable, "-c", _same_pgid_parent_script(pidfile, parent_exit=0)],
                timeout=5,
            )
            child_pid = _wait_for_pidfile(pidfile)
            assert result.exit_code == 0
            assert result.error is None
            assert result.execution is not None
            assert result.execution["state"] == "COMPLETED"
            assert result.execution["direct_process_terminated_confirmed"] is True
            assert result.execution["process_tree_terminated_confirmed"] is True
            assert _pid_alive(child_pid) is False
        finally:
            if child_pid is not None and _pid_alive(child_pid):
                _force_kill(child_pid)


@pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX-only in this contract")
def test_natural_nonzero_exit_with_same_pgid_child_triggers_cleanup_and_fails_safely():
    with tempfile.TemporaryDirectory() as tmpdir:
        pidfile = os.path.join(tmpdir, "child.json")
        child_pid: int | None = None
        try:
            result = run_subprocess(
                "synthetic-natural-nonzero-child",
                [sys.executable, "-c", _same_pgid_parent_script(pidfile, parent_exit=7)],
                timeout=5,
            )
            child_pid = _wait_for_pidfile(pidfile)
            assert result.exit_code == 7
            assert result.execution is not None
            assert result.execution["state"] == "FAILED"
            assert result.execution["direct_process_terminated_confirmed"] is True
            assert result.execution["process_tree_terminated_confirmed"] is True
            assert _pid_alive(child_pid) is False
        finally:
            if child_pid is not None and _pid_alive(child_pid):
                _force_kill(child_pid)


@pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX-only in this contract")
def test_natural_exit_with_unconfirmed_cleanup_is_termination_unconfirmed(monkeypatch, tmp_path):
    from athena import bridge

    pidfile = tmp_path / "child.json"
    child_pid: int | None = None
    real_terminate = bridge.terminate_process_tree
    observed = {"called": False}

    def fake_terminate(proc, pgid, *, grace_s):
        observed["called"] = True
        # Ensure test does not leak even when we force an unconfirmed return.
        real_terminate(proc, pgid, grace_s=grace_s)
        return True, False

    monkeypatch.setattr(bridge, "terminate_process_tree", fake_terminate)
    monkeypatch.setattr(bridge, "_process_group_is_empty", lambda _pgid: False)

    try:
        result = bridge.run_subprocess(
            "synthetic-natural-unconfirmed",
            [sys.executable, "-c", _same_pgid_parent_script(str(pidfile), parent_exit=0)],
            timeout=5,
        )
        child_pid = _wait_for_pidfile(str(pidfile))
        assert observed["called"] is True
        assert result.exit_code != 0
        assert result.error is not None
        assert result.execution is not None
        assert result.execution["state"] == "TERMINATION_UNCONFIRMED"
        assert result.execution["direct_process_terminated_confirmed"] is True
        assert result.execution["process_tree_terminated_confirmed"] is False
        assert result.telemetry is not None
        assert result.telemetry["original_returncode"] == 0
        assert _pid_alive(child_pid) is False
    finally:
        if child_pid is not None and _pid_alive(child_pid):
            _force_kill(child_pid)


@pytest.mark.skipif(not HAS_PTY or sys.platform == "win32", reason="PTY/process groups are POSIX-only")
def test_pty_natural_zero_exit_with_same_pgid_child_triggers_cleanup_and_completes():
    with tempfile.TemporaryDirectory() as tmpdir:
        pidfile = os.path.join(tmpdir, "child.json")
        child_pid: int | None = None
        try:
            result = run_with_pty(
                "synthetic-pty-natural-zero-child",
                [sys.executable, "-c", _same_pgid_parent_script(pidfile, parent_exit=0)],
                timeout=5,
            )
            child_pid = _wait_for_pidfile(pidfile)
            assert result.exit_code == 0
            assert result.error is None
            assert result.execution is not None
            assert result.execution["state"] == "COMPLETED"
            assert result.execution["process_tree_terminated_confirmed"] is True
            assert _pid_alive(child_pid) is False
        finally:
            if child_pid is not None and _pid_alive(child_pid):
                _force_kill(child_pid)


def test_slow_heartbeat_task_completes_within_timeout():
    """A task that dribbles out periodic heartbeat lines before finishing
    should complete normally (no timeout) and preserve all heartbeats."""
    script = (
        "import sys, time\n"
        "for i in range(3):\n"
        "    print(f'heartbeat-{i}')\n"
        "    sys.stdout.flush()\n"
        "    time.sleep(0.2)\n"
        "print('done')\n"
    )

    result = run_subprocess(
        "synthetic-heartbeat",
        [sys.executable, "-c", script],
        timeout=5,
    )

    assert result.timed_out is False
    assert result.exit_code == 0
    for i in range(3):
        assert f"heartbeat-{i}" in result.stdout
    assert "done" in result.stdout
    assert result.duration_s >= 0.5


def test_silent_blocked_task_times_out_promptly():
    """A task that blocks without ever producing output must still be
    killed at the timeout, not left to run for its full sleep duration."""
    script = "import time\ntime.sleep(30)\n"

    result = run_subprocess(
        "synthetic-silent-block",
        [sys.executable, "-c", script],
        timeout=1,
    )

    assert result.timed_out is True
    assert result.exit_code == 124
    assert result.stdout == ""
    assert result.stderr == ""
    # Must be killed near the requested timeout, not left running for the
    # full 30s sleep.
    assert result.duration_s < 10.0


def test_result_arriving_just_under_deadline_is_not_treated_as_timeout():
    """A task that finishes shortly before the deadline is a normal success.

    Timing contract: run_subprocess() delegates directly to
    subprocess.run(timeout=...), which enforces the deadline with a
    monotonic-clock-based wait rather than coarse polling, so it is precise
    to well under 100ms on macOS/Linux CI. Asserting exact equality with the
    deadline is still unsafe (process spawn + interpreter startup jitter can
    itself cost tens of ms under load), so this test sleeps to 80% of the
    2s timeout — a 0.4s margin that is comfortably inside the deadline while
    still exercising the near-boundary path, not a "finishes almost
    instantly" case like the far-under-deadline sanity checks elsewhere in
    this file.
    """
    script = "import time\ntime.sleep(1.6)\nprint('ok-under-deadline')\n"

    result = run_subprocess(
        "synthetic-boundary-under",
        [sys.executable, "-c", script],
        timeout=2,
    )

    assert result.timed_out is False
    assert result.exit_code == 0
    assert "ok-under-deadline" in result.stdout
    assert result.duration_s < 2.0


def test_result_arriving_just_over_deadline_is_treated_as_timeout():
    """A task that finishes shortly after the deadline must be reported as
    a timeout rather than hanging or racily succeeding.

    Timing contract (see the under-deadline test above for the rationale):
    exact equality with the deadline is not asserted because process-start
    and scheduler jitter make that unreliable on CI. Instead this sleeps to
    120% of the 2s timeout — a 0.4s margin past the deadline, close enough
    to genuinely exercise the boundary while remaining safely outside any
    plausible jitter window.
    """
    script = "import time\ntime.sleep(2.4)\nprint('should-not-be-seen-as-success')\n"

    result = run_subprocess(
        "synthetic-boundary-over",
        [sys.executable, "-c", script],
        timeout=2,
    )

    assert result.timed_out is True
    assert result.exit_code == 124
    assert result.duration_s >= 2.0
    assert result.duration_s < 10.0


def test_process_finishing_after_expired_absolute_deadline_is_not_reported_as_success():
    """A process observed as finished (proc.poll() is not None) *after* its
    absolute deadline already expired must still be reported as a timeout,
    never as success -- the deadline-watch loop in run_subprocess() must
    check the deadline before it accepts a poll()-observed completion.

    Timing contract: a very short absolute timeout (1s) with a script that
    sleeps to 130% of it (0.3s margin) — comfortably past both the deadline
    and the 0.05s deadline-poll interval, so this is not a coin-flip on
    scheduler jitter, while staying deterministic and fast (no real CLIs,
    no network).
    """
    script = "import time\ntime.sleep(1.3)\nprint('should-not-be-seen-as-success')\n"

    result = run_subprocess(
        "synthetic-absolute-cancellation-path",
        [sys.executable, "-c", script],
        timeout=1,
    )

    assert result.timed_out is True
    assert result.exit_code == 124
    assert result.duration_s >= 1.0
    assert result.duration_s < 10.0

    execution = result.execution
    assert execution is not None
    assert execution["state"] in ("CANCELLED", "TERMINATION_UNCONFIRMED")
    assert execution["termination_reason"] == "Timeout após 1s"

    to_states = [t["to_state"] for t in execution["history"]]
    assert "CANCELLATION_REQUESTED" in to_states
    assert "TERMINATING" in to_states
    cancellation_transition = next(
        t for t in execution["history"] if t["to_state"] == "CANCELLATION_REQUESTED"
    )
    assert cancellation_transition["reason"] == "Timeout após 1s"


# --- athena.execution: ExecutionRecord state machine (Sprint A2) ----------


def test_valid_transition_sequence_reaches_completed():
    record = ExecutionRecord(provider="synthetic")
    assert record.state is ExecutionState.QUEUED
    record.transition(ExecutionState.STARTING)
    record.transition(ExecutionState.RUNNING)
    record.record_progress()
    assert record.last_progress_at_utc is not None
    record.transition(ExecutionState.COMPLETED)
    assert record.state is ExecutionState.COMPLETED
    assert record.is_terminal is True


def test_cancellation_path_through_terminating_reaches_cancelled():
    record = ExecutionRecord(provider="synthetic")
    record.transition(ExecutionState.STARTING)
    record.transition(ExecutionState.RUNNING)
    record.transition(ExecutionState.CANCELLATION_REQUESTED, reason="client requested cancel")
    record.transition(ExecutionState.TERMINATING)
    record.transition(ExecutionState.CANCELLED)
    assert record.state is ExecutionState.CANCELLED
    assert record.termination_reason == "client requested cancel"


def test_invalid_transition_is_rejected():
    record = ExecutionRecord(provider="synthetic")
    with pytest.raises(InvalidTransitionError):
        record.transition(ExecutionState.RUNNING)  # QUEUED -> RUNNING skips STARTING
    # A rejected transition must not mutate state.
    assert record.state is ExecutionState.QUEUED


@pytest.mark.parametrize("terminal_state", sorted(TERMINAL_STATES, key=lambda s: s.value))
def test_terminal_states_reject_all_further_transitions(terminal_state):
    record = ExecutionRecord(provider="synthetic")
    record.state = terminal_state  # force into the terminal state under test
    for target in ExecutionState:
        with pytest.raises(InvalidTransitionError):
            record.transition(target)
    assert record.state is terminal_state


def test_progress_is_metadata_not_a_separate_state():
    record = ExecutionRecord(provider="synthetic")
    record.transition(ExecutionState.STARTING)
    record.transition(ExecutionState.RUNNING)
    before = record.last_progress_at_monotonic
    assert before is None
    record.record_progress()
    assert record.state is ExecutionState.RUNNING
    assert record.last_progress_at_monotonic is not None
    # Calling it outside RUNNING is a documented no-op, not an error.
    record.transition(ExecutionState.COMPLETED)
    stamp_before = record.last_progress_at_monotonic
    record.record_progress()
    assert record.last_progress_at_monotonic == stamp_before


def test_process_created_defaults_false_and_flips_true_on_identity_assignment():
    record = ExecutionRecord(provider="synthetic")
    assert record.process_created is False
    record.set_process_identity(pid=1234, pgid=1234)
    assert record.process_created is True
    assert record.to_dict()["process_created"] is True


def test_serialization_excludes_sensitive_content():
    record = ExecutionRecord(provider="synthetic-cli")
    record.transition(ExecutionState.STARTING)
    record.transition(ExecutionState.RUNNING)
    record.transition(ExecutionState.FAILED, reason="exit 1")
    payload = record.to_dict()

    forbidden_keys = {
        "prompt",
        "response",
        "command",
        "credential",
        "credentials",
        "secret",
        "api_key",
        "token",
    }
    assert forbidden_keys.isdisjoint(payload.keys())

    serialized = str(payload)
    for needle in ("sk-super-secret-token", "SECRET_PROMPT_TEXT", "AKIA_FAKE_CREDENTIAL"):
        assert needle not in serialized


def test_serialization_is_json_safe_and_has_expected_identity_fields():
    import json

    record = ExecutionRecord(provider="synthetic", profile="fast")
    record.transition(ExecutionState.STARTING)
    payload = record.to_dict()
    encoded = json.dumps(payload)  # must not raise
    assert json.loads(encoded) == payload
    assert payload["execution_id"]
    assert payload["attempt_id"]
    assert payload["execution_id"] != payload["attempt_id"]
    assert payload["provider"] == "synthetic"
    assert payload["profile"] == "fast"


def test_execution_record_emits_updates_and_never_serializes_callback():
    updates: list[dict] = []
    record = ExecutionRecord(provider="synthetic", on_update=lambda snapshot: updates.append(snapshot))
    record.transition(ExecutionState.STARTING)
    record.transition(ExecutionState.RUNNING)
    record.record_progress()
    record.transition(ExecutionState.COMPLETED)
    payload = record.to_dict()

    assert len(updates) >= 4
    assert "on_update" not in payload


@pytest.mark.parametrize(
    "runner",
    [run_subprocess, *([run_with_pty] if HAS_PTY else [])],
    ids=["subprocess", *(["pty"] if HAS_PTY else [])],
)
def test_runner_result_carries_execution_metadata_for_success(runner):
    result = runner(
        "synthetic-exec-success",
        [sys.executable, "-c", "print('ok')"],
        timeout=5,
    )
    assert result.execution is not None
    assert result.execution["state"] == "COMPLETED"
    assert result.execution["is_terminal"] is True
    assert result.execution["provider"] == "synthetic-exec-success"


def test_remote_execution_natural_success_keeps_remote_unconfirmed():
    result = run_subprocess(
        "synthetic-remote-natural-success",
        [sys.executable, "-c", "print('ok')"],
        timeout=5,
        remote_execution=True,
    )
    assert result.exit_code == 0
    assert result.execution is not None
    assert result.execution["transport"] == "ssh"
    assert result.execution["state"] == "COMPLETED"
    assert result.execution["remote_session_started"] is True
    assert result.execution["remote_termination_confirmed"] is False


def test_remote_execution_timeout_is_termination_unconfirmed_even_after_local_kill():
    result = run_subprocess(
        "synthetic-remote-timeout",
        [sys.executable, "-c", "import time; time.sleep(30)"],
        timeout=1,
        remote_execution=True,
    )
    assert result.timed_out is True
    assert result.execution is not None
    assert result.execution["transport"] == "ssh"
    assert result.execution["state"] == "TERMINATION_UNCONFIRMED"
    assert result.execution["remote_session_started"] is True
    assert result.execution["remote_termination_confirmed"] is False


@pytest.mark.parametrize(
    "runner",
    [run_subprocess, *([run_with_pty] if HAS_PTY else [])],
    ids=["subprocess", *(["pty"] if HAS_PTY else [])],
)
def test_runner_result_carries_execution_metadata_for_timeout(runner):
    result = runner(
        "synthetic-exec-timeout",
        [sys.executable, "-c", "import time; time.sleep(30)"],
        timeout=1,
    )

    # Transport-level outcome: the subprocess wrapper reports a plain timeout.
    assert result.timed_out is True
    assert result.error is not None
    assert result.exit_code == 124

    # Execution-contract outcome: never the coarser TIMED_OUT bucket -- the
    # cancellation pipeline always lands in a proper terminal cancellation
    # state. On POSIX (process groups available) this process has no
    # descendants and no process-group escapees, so the owned process group
    # is fully torn down and positively confirmed empty, landing in
    # CANCELLED. On Windows (no process-group confirmation available by
    # design) it lands in TERMINATION_UNCONFIRMED instead.
    assert result.execution is not None
    assert result.execution["is_terminal"] is True
    assert result.execution["direct_process_terminated_confirmed"] is True
    assert result.execution["fallback_started"] is False

    if sys.platform == "win32":
        assert result.execution["state"] == "TERMINATION_UNCONFIRMED"
        assert result.execution["process_tree_terminated_confirmed"] is False
        expected_final = "TERMINATION_UNCONFIRMED"
    else:
        assert result.execution["state"] == "CANCELLED"
        assert result.execution["process_tree_terminated_confirmed"] is True
        expected_final = "CANCELLED"

    # The history must show the full cancellation pipeline, in order, ending
    # at the terminal state.
    history_targets = [t["to_state"] for t in result.execution["history"]]
    assert history_targets[-3:] == [
        "CANCELLATION_REQUESTED",
        "TERMINATING",
        expected_final,
    ]


def test_execution_serialization_has_utc_and_monotonic_timing_keys():
    record = ExecutionRecord(provider="synthetic")
    record.transition(ExecutionState.STARTING)
    record.transition(ExecutionState.RUNNING)
    record.record_progress()
    record.transition(ExecutionState.COMPLETED)
    payload = record.to_dict()

    for key in (
        "created_at_utc",
        "created_at_monotonic",
        "state_entered_at_utc",
        "state_entered_at_monotonic",
        "last_progress_at_utc",
        "last_progress_at_monotonic",
    ):
        assert key in payload

    assert isinstance(payload["created_at_utc"], str)
    assert isinstance(payload["created_at_monotonic"], float)
    assert isinstance(payload["state_entered_at_utc"], str)
    assert isinstance(payload["state_entered_at_monotonic"], float)
    assert isinstance(payload["last_progress_at_utc"], str)
    assert isinstance(payload["last_progress_at_monotonic"], float)

    for entry in payload["history"]:
        assert "at_utc" in entry
        assert "at_monotonic" in entry
        assert isinstance(entry["at_utc"], str)
        assert isinstance(entry["at_monotonic"], float)


def test_synthetic_nonzero_exit_marks_execution_failed():
    result = run_subprocess(
        "synthetic-exec-nonzero-exit",
        [sys.executable, "-c", "import sys; sys.exit(3)"],
        timeout=5,
    )

    assert result.exit_code == 3
    assert result.timed_out is False
    assert result.execution is not None
    assert result.execution["state"] == "FAILED"
    # A normal (non-timeout) nonzero exit is still a direct-process
    # termination the OS already confirmed via proc.poll()/proc.wait() --
    # this must be reflected in the execution metadata or the router has no
    # positive signal to safely allow fallback to the next provider.
    assert result.execution["direct_process_terminated_confirmed"] is True


def test_synthetic_zero_exit_marks_direct_termination_confirmed():
    result = run_subprocess(
        "synthetic-exec-zero-exit",
        [sys.executable, "-c", "print('ok')"],
        timeout=5,
    )

    assert result.exit_code == 0
    assert result.execution is not None
    assert result.execution["state"] == "COMPLETED"
    assert result.execution["direct_process_terminated_confirmed"] is True


def test_run_subprocess_preserves_explicit_execution_and_attempt_ids():
    result = run_subprocess(
        "synthetic-explicit-ids",
        [sys.executable, "-c", "print('ok')"],
        timeout=5,
        execution_id="exec-123",
        attempt_id="attempt-abc",
    )
    assert result.execution is not None
    assert result.execution["execution_id"] == "exec-123"
    assert result.execution["attempt_id"] == "attempt-abc"


def test_cancel_before_popen_returns_cancelled_without_spawning(monkeypatch):
    control = ExecutionControl()
    control.request_cancel(reason="free text user reason")
    called = {"value": False}

    def _forbidden_popen(*_args, **_kwargs):
        called["value"] = True
        raise AssertionError("Popen should not be called")

    monkeypatch.setattr(subprocess, "Popen", _forbidden_popen)
    result = run_subprocess(
        "synthetic-cancel-pre-popen",
        [sys.executable, "-c", "print('noop')"],
        timeout=5,
        execution_control=control,
    )
    assert called["value"] is False
    assert result.exit_code == 130
    assert result.timed_out is False
    assert result.execution is not None
    assert result.execution["state"] == "CANCELLED"
    assert result.execution["process_created"] is False
    assert result.execution["termination_reason"] == "user_requested"


@pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX-only in this contract")
def test_cancel_active_subprocess_transitions_to_cancelled_and_confirms_tree():
    control = ExecutionControl()
    started = threading.Event()
    script = (
        "import sys, time\n"
        "print('ready')\n"
        "sys.stdout.flush()\n"
        "time.sleep(30)\n"
    )

    def _request_cancel():
        assert started.wait(timeout=5)
        time.sleep(0.2)
        control.request_cancel(reason="free text external")

    threading.Thread(target=_request_cancel, daemon=True).start()
    result = run_subprocess(
        "synthetic-cancel-active",
        [sys.executable, "-c", script],
        timeout=60,
        termination_grace_s=0.2,
        on_execution_update=lambda snapshot: started.set() if snapshot.get("state") == "RUNNING" else None,
        execution_control=control,
    )
    assert result.exit_code == 130
    assert result.timed_out is False
    assert result.execution is not None
    assert result.execution["state"] == "CANCELLED"
    assert result.execution["process_tree_terminated_confirmed"] is True
    assert result.execution["termination_reason"] == "user_requested"
    assert result.duration_s < 2.0


@pytest.mark.skipif(not HAS_PTY, reason="PTY creation is POSIX-only in this contract")
def test_cancel_active_pty_transitions_to_terminal_cancel_state():
    control = ExecutionControl()
    started = threading.Event()
    script = "import time; print('ready'); time.sleep(30)"

    def _request_cancel():
        assert started.wait(timeout=5)
        time.sleep(0.2)
        control.request_cancel(reason="free text external")

    threading.Thread(target=_request_cancel, daemon=True).start()
    result = run_with_pty(
        "synthetic-pty-cancel-active",
        [sys.executable, "-c", script],
        timeout=60,
        on_execution_update=lambda snapshot: started.set() if snapshot.get("state") == "RUNNING" else None,
        execution_control=control,
    )
    assert result.exit_code == 130
    assert result.timed_out is False
    assert result.execution is not None
    assert result.execution["state"] in ("CANCELLED", "TERMINATION_UNCONFIRMED")


@pytest.mark.skipif(not HAS_PTY, reason="PTY creation is POSIX-only in this contract")
def test_cancel_active_pty_remote_stays_termination_unconfirmed():
    control = ExecutionControl()
    started = threading.Event()
    script = "import time; print('ready'); time.sleep(30)"

    def _request_cancel():
        assert started.wait(timeout=5)
        time.sleep(0.2)
        control.request_cancel(reason="free text external")

    threading.Thread(target=_request_cancel, daemon=True).start()
    result = run_with_pty(
        "synthetic-pty-cancel-remote",
        [sys.executable, "-c", script],
        timeout=60,
        on_execution_update=lambda snapshot: started.set() if snapshot.get("state") == "RUNNING" else None,
        execution_control=control,
        remote_execution=True,
    )
    assert result.exit_code == 130
    assert result.execution is not None
    assert result.execution["state"] == "TERMINATION_UNCONFIRMED"
    assert result.execution["remote_session_started"] is True
    assert result.execution["remote_termination_confirmed"] is False


def test_run_subprocess_post_launch_oserror_remote_transitions_via_terminating(monkeypatch):
    from athena import bridge

    real_sleep = bridge.time.sleep
    calls = {"count": 0}

    def flaky_sleep(seconds: float) -> None:
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("synthetic post-launch os error")
        real_sleep(seconds)

    monkeypatch.setattr(bridge.time, "sleep", flaky_sleep)
    result = bridge.run_subprocess(
        "synthetic-post-launch-oserror-remote",
        [sys.executable, "-c", "import time; time.sleep(30)"],
        timeout=10,
        remote_execution=True,
    )

    assert result.exit_code == 1
    assert result.execution is not None
    assert result.execution["process_created"] is True
    assert result.execution["state"] == "TERMINATION_UNCONFIRMED"
    assert result.execution["direct_process_terminated_confirmed"] is True
    assert result.execution["process_tree_terminated_confirmed"] is True
    assert result.execution["remote_termination_confirmed"] is False
    assert result.execution["history"][-2]["to_state"] == "TERMINATING"
    assert result.execution["history"][-1]["to_state"] == "TERMINATION_UNCONFIRMED"
    assert _pid_alive(int(result.execution["pid"])) is False


def test_run_subprocess_post_launch_oserror_local_finishes_failed_with_confirmations(monkeypatch):
    from athena import bridge

    real_sleep = bridge.time.sleep
    calls = {"count": 0}

    def flaky_sleep(seconds: float) -> None:
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("synthetic post-launch os error")
        real_sleep(seconds)

    monkeypatch.setattr(bridge.time, "sleep", flaky_sleep)
    result = bridge.run_subprocess(
        "synthetic-post-launch-oserror-local",
        [sys.executable, "-c", "import time; time.sleep(30)"],
        timeout=10,
        remote_execution=False,
    )

    assert result.exit_code == 1
    assert result.execution is not None
    assert result.execution["process_created"] is True
    assert result.execution["state"] == "FAILED"
    assert result.execution["direct_process_terminated_confirmed"] is True
    assert result.execution["process_tree_terminated_confirmed"] is True
    assert result.execution["history"][-2]["to_state"] == "TERMINATING"
    assert result.execution["history"][-1]["to_state"] == "FAILED"
    assert _pid_alive(int(result.execution["pid"])) is False


@pytest.mark.skipif(not HAS_PTY, reason="PTY creation is POSIX-only in this contract")
def test_run_with_pty_post_launch_oserror_remote_transitions_via_terminating(monkeypatch):
    from athena import bridge

    real_select = bridge.select.select
    calls = {"count": 0}

    def flaky_select(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("synthetic pty post-launch os error")
        return real_select(*args, **kwargs)

    monkeypatch.setattr(bridge.select, "select", flaky_select)
    result = bridge.run_with_pty(
        "synthetic-pty-post-launch-oserror-remote",
        [sys.executable, "-c", "import time; time.sleep(30)"],
        timeout=10,
        remote_execution=True,
    )

    assert result.exit_code == 1
    assert result.execution is not None
    assert result.execution["process_created"] is True
    assert result.execution["state"] == "TERMINATION_UNCONFIRMED"
    assert result.execution["direct_process_terminated_confirmed"] is True
    assert result.execution["process_tree_terminated_confirmed"] is True
    assert result.execution["remote_termination_confirmed"] is False
    assert result.execution["history"][-2]["to_state"] == "TERMINATING"
    assert result.execution["history"][-1]["to_state"] == "TERMINATION_UNCONFIRMED"
    assert _pid_alive(int(result.execution["pid"])) is False


@pytest.mark.skipif(not HAS_PTY, reason="PTY creation is POSIX-only in this contract")
def test_run_with_pty_post_launch_filenotfound_remote_transitions_via_terminating(monkeypatch):
    from athena import bridge

    real_select = bridge.select.select
    calls = {"count": 0}

    def flaky_select(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise FileNotFoundError("synthetic pty post-launch fnf")
        return real_select(*args, **kwargs)

    monkeypatch.setattr(bridge.select, "select", flaky_select)
    result = bridge.run_with_pty(
        "synthetic-pty-post-launch-fnf-remote",
        [sys.executable, "-c", "import time; time.sleep(30)"],
        timeout=10,
        remote_execution=True,
    )

    assert result.exit_code == 127
    assert result.execution is not None
    assert result.execution["process_created"] is True
    assert result.execution["state"] == "TERMINATION_UNCONFIRMED"
    assert result.execution["direct_process_terminated_confirmed"] is True
    assert result.execution["process_tree_terminated_confirmed"] is True
    assert result.execution["remote_termination_confirmed"] is False
    assert result.execution["history"][-2]["to_state"] == "TERMINATING"
    assert result.execution["history"][-1]["to_state"] == "TERMINATION_UNCONFIRMED"
    assert _pid_alive(int(result.execution["pid"])) is False


def test_completion_vs_cancel_race_keeps_terminal_coherent():
    control = ExecutionControl()
    started = threading.Event()

    def _request_cancel() -> None:
        assert started.wait(timeout=3)
        time.sleep(0.1)
        control.request_cancel(reason="free text external")

    canceller = threading.Thread(target=_request_cancel, daemon=True)
    canceller.start()
    try:
        result = run_subprocess(
            "synthetic-race-cancel-finish",
            [
                sys.executable,
                "-c",
                (
                    "import sys, time\n"
                    "print('ready')\n"
                    "sys.stdout.flush()\n"
                    "time.sleep(0.15)\n"
                    "print('done')\n"
                ),
            ],
            timeout=5,
            termination_grace_s=0.2,
            on_execution_update=lambda snapshot: (
                started.set() if snapshot.get("state") == "RUNNING" else None
            ),
            execution_control=control,
        )
    except InvalidTransitionError as exc:
        pytest.fail(f"InvalidTransition raised in completion/cancel race: {exc}")
    assert started.is_set()
    canceller.join(timeout=1)
    assert result.execution is not None
    assert result.execution["state"] in {"COMPLETED", "CANCELLED", "TERMINATION_UNCONFIRMED"}
    assert result.execution["is_terminal"] is True
    assert result.execution["pid"] is not None
    assert _pid_alive(int(result.execution["pid"])) is False


def test_run_with_pty_fallback_to_subprocess_propagates_execution_ids(monkeypatch):
    from athena import bridge

    captured: dict[str, object] = {}

    def fake_run_subprocess(provider, command, **kwargs):
        captured["provider"] = provider
        captured["command"] = list(command)
        captured["execution_id"] = kwargs.get("execution_id")
        captured["attempt_id"] = kwargs.get("attempt_id")
        return run_subprocess(provider, command, **kwargs)

    monkeypatch.setattr(bridge, "HAS_PTY", False)
    monkeypatch.setattr(bridge, "run_subprocess", fake_run_subprocess)

    result = bridge.run_with_pty(
        "synthetic-pty-fallback",
        [sys.executable, "-c", "print('ok')"],
        timeout=5,
        execution_id="exec-fallback",
        attempt_id="attempt-fallback",
    )

    assert captured["provider"] == "synthetic-pty-fallback"
    assert captured["execution_id"] == "exec-fallback"
    assert captured["attempt_id"] == "attempt-fallback"
    assert result.execution is not None
    assert result.execution["execution_id"] == "exec-fallback"
    assert result.execution["attempt_id"] == "attempt-fallback"


@pytest.mark.skipif(not HAS_PTY, reason="PTY creation is POSIX-only in this contract")
@pytest.mark.parametrize(
    "runner",
    [run_subprocess, *([run_with_pty] if HAS_PTY else [])],
    ids=["subprocess", *(["pty"] if HAS_PTY else [])],
)
def test_synthetic_missing_command_is_classified_no_process_created(runner):
    """A command that does not exist raises FileNotFoundError inside
    Popen() -- no OS process is ever created, so `process_created` must
    stay False and no termination confirmation should be claimed (there is
    nothing to have terminated)."""
    result = runner(
        "synthetic-missing-command",
        ["athena-mcp-nonexistent-command-xyz"],
        timeout=5,
    )

    assert result.exit_code == 127
    assert result.timed_out is False
    assert result.execution is not None
    assert result.execution["state"] == "FAILED"
    assert result.execution["process_created"] is False
    assert result.execution["pid"] is None
    assert result.execution["direct_process_terminated_confirmed"] is False
    assert result.execution["process_tree_terminated_confirmed"] is False


def test_remote_execution_prelaunch_missing_command_has_no_remote_session():
    result = run_subprocess(
        "synthetic-remote-missing-command",
        ["athena-mcp-nonexistent-command-xyz"],
        timeout=5,
        remote_execution=True,
    )
    assert result.exit_code == 127
    assert result.execution is not None
    assert result.execution["transport"] == "ssh"
    assert result.execution["process_created"] is False
    assert result.execution["remote_session_started"] is False
    assert result.execution["remote_termination_confirmed"] is None


def test_run_with_pty_creates_exactly_one_pty_per_run(monkeypatch):
    """run_with_pty() must call pty.openpty() exactly once per invocation --
    a second call would leak an unused master/slave fd pair (or, worse,
    indicate the transport is racing two PTYs for a single logical run)."""
    import pty as pty_module

    from athena import bridge

    real_openpty = pty_module.openpty
    calls = {"count": 0}

    def counting_openpty():
        calls["count"] += 1
        return real_openpty()

    monkeypatch.setattr(bridge.pty, "openpty", counting_openpty)

    result = run_with_pty(
        "synthetic-pty-single-creation",
        [sys.executable, "-c", "print('ok')"],
        timeout=5,
    )

    assert calls["count"] == 1
    assert result.exit_code == 0


# --- athena.bridge: process-group ownership and termination (Sprint A3) ---


@pytest.mark.skipif(not HAS_PTY, reason="process groups are POSIX-only in this contract")
def test_owned_process_is_its_own_group_leader():
    """On POSIX the direct child is launched as a new session/group leader,
    so `pgid` is populated and equals `pid` -- this is the ownership the
    termination path relies on to safely signal only processes it started."""
    result = run_subprocess(
        "synthetic-pgid-identity",
        [sys.executable, "-c", "print('ok')"],
        timeout=5,
    )
    assert result.execution is not None
    assert result.execution["pid"] is not None
    assert result.execution["pgid"] == result.execution["pid"]


@pytest.mark.skipif(not HAS_PTY, reason="process groups are POSIX-only in this contract")
def test_terminate_process_tree_is_idempotent():
    """Calling terminate_process_tree twice on the same (already-dead)
    process/pgid must not raise -- a second cancellation request racing (or
    following) the first must be a safe no-op, not an error."""
    from athena.bridge import terminate_process_tree

    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        start_new_session=True,
    )
    pgid = os.getpgid(proc.pid)
    try:
        first = terminate_process_tree(proc, pgid)
        second = terminate_process_tree(proc, pgid)
        assert first == (True, True)
        assert second == (True, True)
        assert _pid_alive(proc.pid) is False
    finally:
        if _pid_alive(proc.pid):
            _force_kill(proc.pid)


@pytest.mark.skipif(not HAS_PTY, reason="process groups are POSIX-only in this contract")
def test_terminate_process_tree_reaps_same_group_survivor_after_leader_exits():
    """The direct leader can honor SIGTERM and exit on its own while a plain
    child it spawned (no setsid() of its own, so it stays in the leader's
    pgid) ignores SIGTERM and keeps running.

    terminate_process_tree() must not stop at "the leader is gone" -- it has
    to inspect the pgid itself, notice the survivor, SIGKILL the group, and
    only then report the tree as confirmed empty.
    """
    from athena.bridge import terminate_process_tree

    with tempfile.TemporaryDirectory() as tmpdir:
        pidfile = os.path.join(tmpdir, "child.pid")

        child_script = (
            "import signal, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "time.sleep(30)\n"
        )
        # No SIGTERM handler here: the leader takes the default action
        # (terminate) as soon as the group signal arrives, while the child
        # below -- spawned without start_new_session, so it shares the
        # leader's pgid rather than escaping to one of its own -- ignores it.
        leader_script = (
            "import subprocess, sys, time\n"
            f"child = subprocess.Popen([sys.executable, '-c', {child_script!r}])\n"
            f"with open({pidfile!r}, 'w') as f:\n"
            "    f.write(str(child.pid))\n"
            "    f.flush()\n"
            "time.sleep(30)\n"
        )

        proc = subprocess.Popen(
            [sys.executable, "-c", leader_script],
            start_new_session=True,
        )
        pgid = os.getpgid(proc.pid)
        child_pid: int | None = None
        try:
            deadline = time.monotonic() + 5.0
            while not os.path.exists(pidfile) and time.monotonic() < deadline:
                time.sleep(0.05)
            assert os.path.exists(pidfile), "leader never reported the child pid"
            with open(pidfile) as f:
                child_pid = int(f.read().strip())
            assert _pid_alive(child_pid) is True

            direct_confirmed, tree_confirmed = terminate_process_tree(
                proc, pgid, grace_s=0.5
            )

            assert direct_confirmed is True
            assert tree_confirmed is True
            assert _pid_alive(proc.pid) is False
            assert _pid_alive(child_pid) is False
        finally:
            if _pid_alive(proc.pid):
                _force_kill(proc.pid)
            if child_pid is not None and _pid_alive(child_pid):
                _force_kill(child_pid)


# --- athena.execution: deterministic deadline classification (Sprint A4) --


def test_deadline_status_is_none_before_any_deadline_configured():
    record = ExecutionRecord(provider="synthetic")
    record.transition(ExecutionState.STARTING)
    record.transition(ExecutionState.RUNNING)
    assert record.deadline_status() is None


def test_deadline_budget_monotonic_decreasing_and_cap():
    budget = DeadlineBudget(1.0)
    first = budget.remaining
    time.sleep(0.05)
    second = budget.remaining
    assert first <= 1.0
    assert second < first
    assert budget.elapsed > 0
    assert budget.child_timeout(0.2) <= 0.2
    assert budget.child_timeout(10.0) <= budget.remaining + 0.01


def test_deadline_budget_expires():
    budget = DeadlineBudget(0.05)
    time.sleep(0.08)
    assert budget.expired is True
    assert budget.remaining == 0.0


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_deadline_budget_rejects_non_finite_total(value):
    with pytest.raises(ValueError):
        DeadlineBudget(value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), 0.0, -1.0, True])
def test_deadline_budget_child_timeout_rejects_invalid_cap(value):
    budget = DeadlineBudget(1.0)
    with pytest.raises(ValueError):
        budget.child_timeout(value)


def test_deadline_status_classifies_absolute_deadline():
    record = ExecutionRecord(provider="synthetic")
    record.transition(ExecutionState.STARTING)
    record.transition(ExecutionState.RUNNING)
    record.configure_deadlines(absolute_deadline_s=10.0)
    now = record.created_at_monotonic + 10.5
    assert record.deadline_status(now) == "absolute_deadline"
    assert record.deadline_status(record.created_at_monotonic + 5.0) is None


def test_deadline_status_classifies_idle_deadline_from_last_progress():
    record = ExecutionRecord(provider="synthetic")
    record.transition(ExecutionState.STARTING)
    record.transition(ExecutionState.RUNNING)
    record.configure_deadlines(absolute_deadline_s=1000.0, idle_deadline_s=5.0)
    record.record_progress()
    still_idle_ok = record.last_progress_at_monotonic + 4.0
    assert record.deadline_status(still_idle_ok) is None
    idle_expired = record.last_progress_at_monotonic + 5.5
    assert record.deadline_status(idle_expired) == "idle_deadline"


def test_deadline_status_absolute_ceiling_wins_over_reset_idle_clock():
    """A heartbeat that keeps resetting the idle clock must not let the run
    escape the absolute ceiling -- absolute always takes priority when both
    are expired at the same instant."""
    record = ExecutionRecord(provider="synthetic")
    record.transition(ExecutionState.STARTING)
    record.transition(ExecutionState.RUNNING)
    record.configure_deadlines(absolute_deadline_s=10.0, idle_deadline_s=2.0)
    record.record_progress()  # heartbeat keeps idle_deadline from firing
    now = record.created_at_monotonic + 10.5
    assert record.deadline_status(now) == "absolute_deadline"


def test_configure_deadlines_populates_termination_grace_s_in_serialization():
    record = ExecutionRecord(provider="synthetic")
    record.configure_deadlines(
        absolute_deadline_s=30.0, idle_deadline_s=5.0, termination_grace_s=1.5
    )
    payload = record.to_dict()
    assert payload["absolute_deadline_s"] == 30.0
    assert payload["idle_deadline_s"] == 5.0
    assert payload["termination_grace_s"] == 1.5


# --- athena.bridge: idle-deadline enforcement over the real transport (A4) -


def test_idle_timeout_kills_task_that_goes_silent_within_a_longer_absolute_timeout():
    """A task prints once, then blocks silently well past idle_timeout but
    well under the absolute timeout -- it must be killed for inactivity, not
    left running until the (much longer) absolute deadline."""
    script = (
        "import sys, time\n"
        "print('first-output')\n"
        "sys.stdout.flush()\n"
        "time.sleep(30)\n"
    )

    result = run_subprocess(
        "synthetic-idle-kill",
        [sys.executable, "-c", script],
        timeout=60,
        idle_timeout=1,
    )

    assert result.timed_out is True
    assert result.exit_code == 124
    assert "first-output" in result.stdout
    assert result.duration_s < 10.0
    assert result.execution is not None
    assert "inatividade" in result.execution["termination_reason"]


def test_idle_timeout_does_not_fire_while_heartbeats_keep_arriving():
    """Periodic heartbeats must keep resetting the idle clock so the task
    completes normally instead of being killed for false inactivity."""
    script = (
        "import sys, time\n"
        "for i in range(4):\n"
        "    print(f'beat-{i}')\n"
        "    sys.stdout.flush()\n"
        "    time.sleep(0.3)\n"
        "print('done')\n"
    )

    result = run_subprocess(
        "synthetic-idle-heartbeat",
        [sys.executable, "-c", script],
        timeout=10,
        idle_timeout=2,
    )

    assert result.timed_out is False
    assert result.exit_code == 0
    assert "done" in result.stdout


def test_absolute_timeout_still_enforced_despite_continuous_heartbeat():
    """Even a task that never goes idle (constant heartbeat resetting
    idle_timeout) must still be killed once the absolute ceiling passes."""
    script = (
        "import sys, time\n"
        "for _ in range(100):\n"
        "    print('beat')\n"
        "    sys.stdout.flush()\n"
        "    time.sleep(0.1)\n"
    )

    result = run_subprocess(
        "synthetic-absolute-ceiling",
        [sys.executable, "-c", script],
        timeout=1,
        idle_timeout=5,
    )

    assert result.timed_out is True
    assert result.exit_code == 124
    assert result.duration_s < 10.0
    assert result.execution is not None
    assert result.execution["termination_reason"] == "Timeout após 1s"
