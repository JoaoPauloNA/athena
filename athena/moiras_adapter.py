"""Optional, observation-only adapter from Athena lifecycle updates to Moiras.

The adapter is disabled by default and never participates in timeout,
cancellation, fallback, lease, verification, or authorization decisions. It
removes Athena-only process metadata, constructs the small Moiras temporal
contract, and stores only the latest inert advisory in memory.

Moiras is imported lazily so Athena has no mandatory runtime dependency on the
standalone project. Missing/incompatible Moiras, invalid input, or comparison
failure becomes a categorical advisory status and never changes Athena's
deterministic behavior.
"""

from __future__ import annotations

import importlib
import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from types import ModuleType
from typing import Any

__all__ = [
    "MoirasAdapterStatus",
    "MoirasAdapterReason",
    "MoirasShadowAdvisory",
    "MoirasShadowObserver",
]


class MoirasAdapterStatus(str, Enum):
    DISABLED = "DISABLED"
    UNAVAILABLE = "UNAVAILABLE"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    OBSERVED = "OBSERVED"
    INDETERMINATE = "INDETERMINATE"


class MoirasAdapterReason(str, Enum):
    EXPLICITLY_DISABLED = "EXPLICITLY_DISABLED"
    PACKAGE_UNAVAILABLE = "PACKAGE_UNAVAILABLE"
    INCOMPATIBLE_PACKAGE = "INCOMPATIBLE_PACKAGE"
    FIRST_SNAPSHOT = "FIRST_SNAPSHOT"
    INVALID_INPUT = "INVALID_INPUT"
    COMPARISON_REJECTED = "COMPARISON_REJECTED"
    CLASSIFICATION_AVAILABLE = "CLASSIFICATION_AVAILABLE"


@dataclass(frozen=True)
class MoirasShadowAdvisory:
    status: MoirasAdapterStatus
    reason: MoirasAdapterReason
    classification: str | None = None
    evidence_codes: tuple[str, ...] = ()
    affects_control_flow: bool = False
    executed: bool = False
    mode: str = "shadow"

    def __post_init__(self) -> None:
        if self.affects_control_flow is not False:
            raise ValueError("Moiras advisory cannot affect Athena control flow")
        if self.executed is not False:
            raise ValueError("Moiras advisory cannot claim execution")
        if self.mode != "shadow":
            raise ValueError("Moiras advisory mode must be shadow")

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "reason": self.reason.value,
            "classification": self.classification,
            "evidence_codes": list(self.evidence_codes),
            "affects_control_flow": self.affects_control_flow,
            "executed": self.executed,
            "mode": self.mode,
        }


@dataclass
class _AttemptObservation:
    previous_snapshot: Any | None = None
    progress_counter: int = 0
    activity_counter: int = 0
    last_progress_marker: float | None = None


_REQUIRED_MOIRAS_API = (
    "ExecutionSnapshot",
    "LifecycleState",
    "compare_snapshots",
    "sanitize_value",
)


class MoirasShadowObserver:
    """Callable-compatible observer for Athena's existing lifecycle callback.

    Pass ``observer.observe`` as ``on_execution_update`` only in an embedding
    that does not already own that callback. The observer stores a bounded,
    process-local advisory; callers may inspect ``latest``. Athena never reads
    it to make a control-flow decision.
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        idle_threshold_s: float = 300.0,
        max_attempts: int = 64,
        module_loader: Callable[[], ModuleType] | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
        utc_now: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        if isinstance(idle_threshold_s, bool) or not isinstance(
            idle_threshold_s, (int, float)
        ):
            raise ValueError("idle_threshold_s must be numeric")
        if not math.isfinite(idle_threshold_s) or idle_threshold_s < 0:
            raise ValueError("idle_threshold_s must be finite and >= 0")
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int):
            raise ValueError("max_attempts must be an integer")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be > 0")

        self._enabled = enabled
        self._idle_threshold_s = float(idle_threshold_s)
        self._max_attempts = max_attempts
        self._module_loader = module_loader or (lambda: importlib.import_module("moiras"))
        self._monotonic_clock = monotonic_clock
        self._utc_now = utc_now or (lambda: datetime.now(timezone.utc))
        self._started_at = monotonic_clock()
        self._module: ModuleType | None = None
        self._load_attempted = False
        self._load_error: MoirasAdapterReason | None = None
        self._attempts: OrderedDict[tuple[str, str], _AttemptObservation] = OrderedDict()
        self._latest = MoirasShadowAdvisory(
            status=(
                MoirasAdapterStatus.INSUFFICIENT_HISTORY
                if enabled
                else MoirasAdapterStatus.DISABLED
            ),
            reason=(
                MoirasAdapterReason.FIRST_SNAPSHOT
                if enabled
                else MoirasAdapterReason.EXPLICITLY_DISABLED
            ),
        )
        self._lock = threading.Lock()

    @property
    def latest(self) -> MoirasShadowAdvisory:
        with self._lock:
            return self._latest

    def _load_module(self) -> tuple[ModuleType | None, MoirasAdapterReason | None]:
        if self._load_attempted:
            if self._module is None:
                return None, self._load_error or MoirasAdapterReason.PACKAGE_UNAVAILABLE
            return self._module, None
        self._load_attempted = True
        try:
            module = self._module_loader()
        except Exception:
            self._load_error = MoirasAdapterReason.PACKAGE_UNAVAILABLE
            return None, MoirasAdapterReason.PACKAGE_UNAVAILABLE
        if any(not hasattr(module, name) for name in _REQUIRED_MOIRAS_API):
            self._load_error = MoirasAdapterReason.INCOMPATIBLE_PACKAGE
            return None, MoirasAdapterReason.INCOMPATIBLE_PACKAGE
        self._module = module
        self._load_error = None
        return module, None

    def _fail(self, reason: MoirasAdapterReason) -> MoirasShadowAdvisory:
        status = (
            MoirasAdapterStatus.UNAVAILABLE
            if reason
            in {
                MoirasAdapterReason.PACKAGE_UNAVAILABLE,
                MoirasAdapterReason.INCOMPATIBLE_PACKAGE,
            }
            else MoirasAdapterStatus.INDETERMINATE
        )
        self._latest = MoirasShadowAdvisory(status=status, reason=reason)
        return self._latest

    def observe(self, metadata: Mapping[str, Any]) -> MoirasShadowAdvisory:
        """Observe one sanitized Athena lifecycle update without ever raising."""

        with self._lock:
            if not self._enabled:
                return self._latest
            module, load_error = self._load_module()
            if module is None:
                return self._fail(load_error or MoirasAdapterReason.PACKAGE_UNAVAILABLE)
            try:
                execution_id = metadata["execution_id"]
                attempt_id = metadata["attempt_id"]
                state_name = metadata["state"]
                if not all(
                    isinstance(value, str) and value
                    for value in (execution_id, attempt_id, state_name)
                ):
                    return self._fail(MoirasAdapterReason.INVALID_INPUT)

                key = (execution_id, attempt_id)
                attempt = self._attempts.setdefault(key, _AttemptObservation())
                self._attempts.move_to_end(key)
                while len(self._attempts) > self._max_attempts:
                    self._attempts.popitem(last=False)

                marker = metadata.get("last_progress_at_monotonic")
                if marker is not None:
                    if isinstance(marker, bool) or not isinstance(marker, (int, float)):
                        return self._fail(MoirasAdapterReason.INVALID_INPUT)
                    if not math.isfinite(marker):
                        return self._fail(MoirasAdapterReason.INVALID_INPUT)
                    marker = float(marker)
                    if marker != attempt.last_progress_marker:
                        attempt.progress_counter += 1
                        attempt.last_progress_marker = marker
                attempt.activity_counter += 1

                lifecycle_state = getattr(
                    module.LifecycleState,
                    state_name,
                    module.LifecycleState.UNKNOWN,
                )
                now_monotonic = self._monotonic_clock()
                if (
                    isinstance(now_monotonic, bool)
                    or not isinstance(now_monotonic, (int, float))
                    or not math.isfinite(now_monotonic)
                ):
                    return self._fail(MoirasAdapterReason.INVALID_INPUT)
                monotonic_offset = max(0.0, float(now_monotonic) - self._started_at)
                captured_at = self._utc_now()

                snapshot = module.ExecutionSnapshot(
                    execution_id=execution_id,
                    attempt_id=attempt_id,
                    profile="athena-shadow",
                    lifecycle_state=lifecycle_state,
                    captured_at_utc=captured_at,
                    monotonic_offset_s=monotonic_offset,
                    progress_counter=attempt.progress_counter,
                    activity_counter=attempt.activity_counter,
                    artifact_revision=0,
                    waiting_for_authorization=(
                        metadata.get("waiting_for_authorization") is True
                    ),
                    waiting_for_credential=(
                        metadata.get("waiting_for_credential") is True
                    ),
                    external_block=metadata.get("external_block") is True,
                    terminal=state_name
                    in {
                        "COMPLETED",
                        "FAILED",
                        "CANCELLED",
                        "TIMED_OUT",
                        "TERMINATION_UNCONFIRMED",
                    },
                )
                module.sanitize_value(snapshot.to_dict())
                if attempt.previous_snapshot is None:
                    attempt.previous_snapshot = snapshot
                    self._latest = MoirasShadowAdvisory(
                        status=MoirasAdapterStatus.INSUFFICIENT_HISTORY,
                        reason=MoirasAdapterReason.FIRST_SNAPSHOT,
                    )
                    return self._latest

                result = module.compare_snapshots(
                    attempt.previous_snapshot,
                    snapshot,
                    idle_threshold_s=self._idle_threshold_s,
                )
                module.sanitize_value(result.to_dict())
                attempt.previous_snapshot = snapshot
                self._latest = MoirasShadowAdvisory(
                    status=MoirasAdapterStatus.OBSERVED,
                    reason=MoirasAdapterReason.CLASSIFICATION_AVAILABLE,
                    classification=result.classification.value,
                    evidence_codes=tuple(code.value for code in result.evidence_codes),
                )
                return self._latest
            except Exception:
                return self._fail(MoirasAdapterReason.COMPARISON_REJECTED)
