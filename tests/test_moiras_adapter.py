import time
from dataclasses import dataclass
from enum import Enum
from types import SimpleNamespace

import pytest

from athena.moiras_adapter import (
    MoirasAdapterReason,
    MoirasAdapterStatus,
    MoirasShadowAdvisory,
    MoirasShadowObserver,
)


class FakeLifecycleState(Enum):
    QUEUED = "QUEUED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    CANCELLATION_REQUESTED = "CANCELLATION_REQUESTED"
    TERMINATING = "TERMINATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    TERMINATION_UNCONFIRMED = "TERMINATION_UNCONFIRMED"
    UNKNOWN = "UNKNOWN"


class FakeClass(Enum):
    REAL_PROGRESS = "REAL_PROGRESS"
    ACTIVITY_WITHOUT_PROGRESS = "ACTIVITY_WITHOUT_PROGRESS"
    PROBABLE_INACTIVITY = "PROBABLE_INACTIVITY"
    LEGITIMATE_WAIT = "LEGITIMATE_WAIT"
    EXTERNAL_BLOCK = "EXTERNAL_BLOCK"
    INDETERMINATE = "INDETERMINATE"


class FakeEvidence(Enum):
    PROGRESS_COUNTER_INCREASED = "PROGRESS_COUNTER_INCREASED"
    ACTIVITY_COUNTER_INCREASED = "ACTIVITY_COUNTER_INCREASED"
    IDLE_THRESHOLD_EXCEEDED = "IDLE_THRESHOLD_EXCEEDED"
    IDLE_THRESHOLD_NOT_EXCEEDED = "IDLE_THRESHOLD_NOT_EXCEEDED"
    WAITING_FOR_CREDENTIAL = "WAITING_FOR_CREDENTIAL"
    EXTERNAL_BLOCK_FLAG = "EXTERNAL_BLOCK_FLAG"


@dataclass
class FakeSnapshot:
    execution_id: str
    attempt_id: str
    profile: str
    lifecycle_state: FakeLifecycleState
    captured_at_utc: object
    monotonic_offset_s: float
    progress_counter: int
    activity_counter: int
    artifact_revision: int
    waiting_for_authorization: bool
    waiting_for_credential: bool
    external_block: bool
    terminal: bool

    def __post_init__(self):
        if "/" in self.execution_id:
            raise ValueError("invalid id")

    def to_dict(self):
        return {
            "execution_id": self.execution_id,
            "attempt_id": self.attempt_id,
            "profile": self.profile,
            "lifecycle_state": self.lifecycle_state.value,
            "monotonic_offset_s": self.monotonic_offset_s,
            "progress_counter": self.progress_counter,
            "activity_counter": self.activity_counter,
            "artifact_revision": self.artifact_revision,
            "waiting_for_authorization": self.waiting_for_authorization,
            "waiting_for_credential": self.waiting_for_credential,
            "external_block": self.external_block,
            "terminal": self.terminal,
        }


@dataclass
class FakeResult:
    classification: FakeClass
    evidence_codes: tuple[FakeEvidence, ...]

    def to_dict(self):
        return {
            "classification": self.classification.value,
            "evidence_codes": [code.value for code in self.evidence_codes],
        }


def fake_module(captured=None, compare_calls=None):
    def sanitize(payload):
        if captured is not None:
            captured.append(payload)
        return payload

    def compare(first, second, *, idle_threshold_s):
        if compare_calls is not None:
            compare_calls.append((first, second))
        if second.progress_counter > first.progress_counter or (
            second.terminal and not first.terminal
        ):
            return FakeResult(
                FakeClass.REAL_PROGRESS,
                (FakeEvidence.PROGRESS_COUNTER_INCREASED,),
            )
        if second.external_block:
            return FakeResult(
                FakeClass.EXTERNAL_BLOCK,
                (FakeEvidence.EXTERNAL_BLOCK_FLAG,),
            )
        if second.waiting_for_credential or second.waiting_for_authorization:
            return FakeResult(
                FakeClass.LEGITIMATE_WAIT,
                (FakeEvidence.WAITING_FOR_CREDENTIAL,),
            )
        if second.activity_counter > first.activity_counter:
            return FakeResult(
                FakeClass.ACTIVITY_WITHOUT_PROGRESS,
                (FakeEvidence.ACTIVITY_COUNTER_INCREASED,),
            )
        if second.monotonic_offset_s - first.monotonic_offset_s >= idle_threshold_s:
            return FakeResult(
                FakeClass.PROBABLE_INACTIVITY,
                (FakeEvidence.IDLE_THRESHOLD_EXCEEDED,),
            )
        return FakeResult(
            FakeClass.INDETERMINATE,
            (FakeEvidence.IDLE_THRESHOLD_NOT_EXCEEDED,),
        )

    return SimpleNamespace(
        __version__="0.1.0",
        SCHEMA_VERSION="1.0",
        ExecutionSnapshot=FakeSnapshot,
        LifecycleState=FakeLifecycleState,
        SentinelClass=FakeClass,
        compare_snapshots=compare,
        sanitize_value=sanitize,
        validate_id=lambda value, **_kwargs: (
            value
            if isinstance(value, str) and value and "/" not in value
            else (_ for _ in ()).throw(ValueError("invalid id"))
        ),
    )


def metadata(**overrides):
    payload = {
        "execution_id": "execution-1",
        "attempt_id": "attempt-1",
        "state": "RUNNING",
        "provider": "provider=codex /private/path",
        "pid": 999,
        "pgid": 999,
        "command": ["secret-command"],
        "last_progress_at_monotonic": None,
        "state_entered_at_monotonic": 1.0,
        "history": [],
    }
    payload.update(overrides)
    return payload


def sequence_clock(*values):
    iterator = iter(values)
    return lambda: next(iterator)


def test_disabled_observer_never_loads_moiras():
    observer = MoirasShadowObserver(
        enabled=False,
        module_loader=lambda: (_ for _ in ()).throw(AssertionError("must not load")),
    )
    result = observer.observe(metadata())
    assert result.status == MoirasAdapterStatus.DISABLED
    assert result.affects_control_flow is False


def test_missing_or_incompatible_package_is_categorical_and_inert():
    missing = MoirasShadowObserver(
        enabled=True,
        module_loader=lambda: (_ for _ in ()).throw(ImportError("private path")),
    )
    result = missing.observe(metadata())
    assert result.status == MoirasAdapterStatus.UNAVAILABLE
    assert result.reason == MoirasAdapterReason.PACKAGE_UNAVAILABLE
    assert "private path" not in str(result.to_dict())

    incompatible = MoirasShadowObserver(
        enabled=True,
        module_loader=lambda: SimpleNamespace(
            ExecutionSnapshot=lambda **kwargs: kwargs,
            LifecycleState=FakeLifecycleState,
            SentinelClass=FakeClass,
            compare_snapshots=lambda *_args, **_kwargs: None,
            sanitize_value=lambda payload: payload,
            validate_id=lambda value, **_kwargs: value,
            SCHEMA_VERSION="999",
            __version__="0.1.0",
        ),
    )
    result = incompatible.observe(metadata())
    assert result.reason == MoirasAdapterReason.INCOMPATIBLE_PACKAGE


def test_two_updates_produce_real_progress_only_when_progress_marker_changes():
    observer = MoirasShadowObserver(
        enabled=True,
        idle_threshold_s=10,
        module_loader=fake_module,
        monotonic_clock=sequence_clock(100, 101, 102),
    )
    first = observer.observe(metadata())
    second = observer.observe(metadata(last_progress_at_monotonic=50.0))
    assert first.status == MoirasAdapterStatus.INSUFFICIENT_HISTORY
    assert second.status == MoirasAdapterStatus.OBSERVED
    assert second.classification == "REAL_PROGRESS"
    assert second.affects_control_flow is False
    assert second.executed is False
    assert observer.latest == second


def test_unchanged_running_snapshot_can_become_probable_inactivity():
    observer = MoirasShadowObserver(
        enabled=True,
        idle_threshold_s=10,
        module_loader=fake_module,
        monotonic_clock=sequence_clock(100, 101, 112),
    )
    observer.observe(metadata())
    result = observer.observe(metadata())
    assert result.classification == "PROBABLE_INACTIVITY"
    assert result.evidence_codes == ("IDLE_THRESHOLD_EXCEEDED",)


def test_state_change_is_activity_without_progress():
    observer = MoirasShadowObserver(
        enabled=True,
        idle_threshold_s=100,
        module_loader=fake_module,
        monotonic_clock=sequence_clock(100, 101, 102),
    )
    observer.observe(metadata(state="STARTING", state_entered_at_monotonic=1.0))
    result = observer.observe(
        metadata(state="RUNNING", state_entered_at_monotonic=2.0, history=[{}])
    )
    assert result.classification == "ACTIVITY_WITHOUT_PROGRESS"


def test_explicit_wait_and_external_block_remain_supported_contract_inputs():
    observer = MoirasShadowObserver(
        enabled=True,
        idle_threshold_s=1,
        module_loader=fake_module,
        monotonic_clock=sequence_clock(100, 101, 103, 104),
    )
    observer.observe(metadata())
    wait = observer.observe(metadata(waiting_for_credential=True))
    block = observer.observe(metadata(external_block=True))
    assert wait.classification == "LEGITIMATE_WAIT"
    assert block.classification == "EXTERNAL_BLOCK"


def test_boundary_drops_sensitive_keys_and_values_before_module_call():
    captured = []
    observer = MoirasShadowObserver(
        enabled=True,
        idle_threshold_s=10,
        module_loader=lambda: fake_module(captured),
        monotonic_clock=sequence_clock(100, 101),
    )
    observer.observe(metadata())
    outbound = captured[0]
    serialized = repr(outbound)
    forbidden_keys = {
        "provider",
        "pid",
        "pgid",
        "command",
        "output",
        "path",
        "host",
        "user",
    }
    assert forbidden_keys.isdisjoint(outbound)
    assert "codex" not in serialized
    assert "/private/path" not in serialized
    assert "secret-command" not in serialized


def test_submit_coalesces_hot_updates_and_does_not_load_module_inline():
    loader_calls = []
    compare_calls = []

    def loader():
        loader_calls.append(True)
        return fake_module(compare_calls=compare_calls)

    observer = MoirasShadowObserver(
        enabled=True,
        idle_threshold_s=100,
        module_loader=loader,
        background_sampling=True,
        sampling_interval_s=0.05,
    )
    try:
        for index in range(1000):
            observer.submit(metadata(last_progress_at_monotonic=float(index)))
        assert loader_calls == []
        deadline = time.monotonic() + 1
        while not loader_calls and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(loader_calls) == 1
        assert len(compare_calls) <= 1
    finally:
        observer.close()


def test_submit_rejects_sensitive_identifier_before_queueing_or_loading_module():
    loader_calls = []
    observer = MoirasShadowObserver(
        enabled=True,
        module_loader=lambda: loader_calls.append(True) or fake_module(),
        background_sampling=True,
        sampling_interval_s=1,
    )
    try:
        result = observer.submit(metadata(execution_id="token-production-1"))
        assert result.reason == MoirasAdapterReason.INVALID_INPUT
        assert loader_calls == []
        assert observer._pending == {}
    finally:
        observer.close()


def test_invalid_metadata_or_comparison_never_raises():
    observer = MoirasShadowObserver(
        enabled=True,
        module_loader=fake_module,
        monotonic_clock=sequence_clock(100, 101),
    )
    result = observer.observe(metadata(execution_id="bad/id"))
    assert result.status == MoirasAdapterStatus.INDETERMINATE
    assert result.reason == MoirasAdapterReason.INVALID_INPUT


def test_advisory_cannot_claim_control_execution_or_unknown_classification():
    with pytest.raises(ValueError):
        MoirasShadowAdvisory(
            status=MoirasAdapterStatus.OBSERVED,
            reason=MoirasAdapterReason.CLASSIFICATION_AVAILABLE,
            affects_control_flow=True,
        )
    with pytest.raises(ValueError):
        MoirasShadowAdvisory(
            status=MoirasAdapterStatus.OBSERVED,
            reason=MoirasAdapterReason.CLASSIFICATION_AVAILABLE,
            executed=True,
        )
    with pytest.raises(ValueError):
        MoirasShadowAdvisory(
            status=MoirasAdapterStatus.OBSERVED,
            reason=MoirasAdapterReason.CLASSIFICATION_AVAILABLE,
            classification="EU_INVENTEI_ISSO",
        )
