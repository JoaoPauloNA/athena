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
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    UNKNOWN = "UNKNOWN"


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


class FakeClass(Enum):
    REAL_PROGRESS = "REAL_PROGRESS"


class FakeEvidence(Enum):
    PROGRESS_COUNTER_INCREASED = "PROGRESS_COUNTER_INCREASED"


@dataclass
class FakeResult:
    classification: FakeClass = FakeClass.REAL_PROGRESS
    evidence_codes: tuple = (FakeEvidence.PROGRESS_COUNTER_INCREASED,)

    def to_dict(self):
        return {
            "classification": self.classification.value,
            "evidence_codes": [code.value for code in self.evidence_codes],
        }


def fake_module(captured=None):
    def sanitize(payload):
        if captured is not None:
            captured.append(payload)
        return payload

    def compare(first, second, *, idle_threshold_s):
        assert second.activity_counter > first.activity_counter
        assert idle_threshold_s == 10
        return FakeResult()

    return SimpleNamespace(
        ExecutionSnapshot=FakeSnapshot,
        LifecycleState=FakeLifecycleState,
        compare_snapshots=compare,
        sanitize_value=sanitize,
    )


def metadata(**overrides):
    payload = {
        "execution_id": "execution-1",
        "attempt_id": "attempt-1",
        "state": "RUNNING",
        "provider": "must-not-cross-boundary",
        "pid": 999,
        "pgid": 999,
        "last_progress_at_monotonic": None,
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

    incompatible = MoirasShadowObserver(enabled=True, module_loader=lambda: SimpleNamespace())
    result = incompatible.observe(metadata())
    assert result.reason == MoirasAdapterReason.INCOMPATIBLE_PACKAGE


def test_two_updates_produce_only_an_inert_advisory():
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


def test_boundary_drops_provider_process_and_other_athena_metadata():
    captured = []
    observer = MoirasShadowObserver(
        enabled=True,
        idle_threshold_s=10,
        module_loader=lambda: fake_module(captured),
        monotonic_clock=sequence_clock(100, 101),
    )
    observer.observe(metadata())
    outbound = captured[0]
    forbidden = {
        "provider",
        "pid",
        "pgid",
        "command",
        "output",
        "path",
        "host",
        "user",
    }
    assert forbidden.isdisjoint(outbound)


def test_invalid_metadata_or_comparison_never_raises():
    observer = MoirasShadowObserver(
        enabled=True,
        module_loader=fake_module,
        monotonic_clock=sequence_clock(100, 101),
    )
    result = observer.observe(metadata(execution_id="bad/id"))
    assert result.status == MoirasAdapterStatus.INDETERMINATE
    assert result.reason == MoirasAdapterReason.COMPARISON_REJECTED


def test_advisory_cannot_claim_control_or_execution():
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
