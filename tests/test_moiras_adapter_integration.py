"""Cross-repository contract tests; skipped unless the real package is installed."""

import time

import pytest

from athena.execution import ExecutionRecord, ExecutionState
from athena.moiras_adapter import MoirasShadowObserver

moiras = pytest.importorskip("moiras")


class Clock:
    def __init__(self, *values):
        self._values = iter(values)

    def __call__(self):
        return next(self._values)


def _metadata(**overrides):
    payload = {
        "execution_id": "execution-real-1",
        "attempt_id": "attempt-real-1",
        "state": "RUNNING",
        "last_progress_at_monotonic": None,
        "state_entered_at_monotonic": 1.0,
        "history": [],
    }
    payload.update(overrides)
    return payload


def test_real_moiras_emits_inactivity_and_progress_from_athena_snapshots():
    observer = MoirasShadowObserver(
        enabled=True,
        idle_threshold_s=10,
        module_loader=lambda: moiras,
        monotonic_clock=Clock(100, 101, 112, 113),
    )
    first = observer.observe(_metadata())
    inactive = observer.observe(_metadata())
    progressed = observer.observe(_metadata(last_progress_at_monotonic=50.0))

    assert first.status.value == "INSUFFICIENT_HISTORY"
    assert inactive.classification == "PROBABLE_INACTIVITY"
    assert progressed.classification == "REAL_PROGRESS"


def test_real_moiras_wait_and_external_block_precede_inactivity():
    observer = MoirasShadowObserver(
        enabled=True,
        idle_threshold_s=1,
        module_loader=lambda: moiras,
        monotonic_clock=Clock(100, 101, 103, 105),
    )
    observer.observe(_metadata())
    wait = observer.observe(_metadata(waiting_for_authorization=True))
    block = observer.observe(_metadata(external_block=True))
    assert wait.classification == "LEGITIMATE_WAIT"
    assert block.classification == "EXTERNAL_BLOCK"


def test_background_sampler_reports_inactivity_without_a_new_lifecycle_event():
    observer = MoirasShadowObserver(
        enabled=True,
        idle_threshold_s=0.05,
        module_loader=lambda: moiras,
        background_sampling=True,
        sampling_interval_s=0.02,
    )
    try:
        observer.submit(_metadata())
        deadline = time.monotonic() + 1
        advisory = None
        while time.monotonic() < deadline:
            advisory = observer.advisory_for("execution-real-1", "attempt-real-1")
            if advisory is not None and advisory.classification == "PROBABLE_INACTIVITY":
                break
            time.sleep(0.01)
        assert advisory is not None
        assert advisory.classification == "PROBABLE_INACTIVITY"
        assert advisory.affects_control_flow is False
        assert advisory.executed is False
    finally:
        observer.close()


def test_real_athena_execution_record_reaches_four_source_supported_classes():
    observer = MoirasShadowObserver(
        enabled=True,
        idle_threshold_s=10,
        module_loader=lambda: moiras,
        monotonic_clock=Clock(100, 101, 102, 103, 113, 114),
    )
    record = ExecutionRecord(
        provider="synthetic",
        execution_id="execution-record-1",
        attempt_id="attempt-record-1",
    )
    record.transition(ExecutionState.STARTING)
    observer.observe(record.to_dict())
    record.transition(ExecutionState.RUNNING)
    activity = observer.observe(record.to_dict())
    indeterminate = observer.observe(record.to_dict())
    inactive = observer.observe(record.to_dict())
    record.record_progress()
    progress = observer.observe(record.to_dict())

    assert activity.classification == "ACTIVITY_WITHOUT_PROGRESS"
    assert indeterminate.classification == "INDETERMINATE"
    assert inactive.classification == "PROBABLE_INACTIVITY"
    assert progress.classification == "REAL_PROGRESS"
