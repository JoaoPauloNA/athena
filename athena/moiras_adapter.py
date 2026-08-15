"""Optional, observation-only adapter from Athena lifecycle updates to Moiras.

The adapter is disabled by default and never participates in timeout,
cancellation, fallback, lease, verification, or authorization decisions.  A
bounded background sampler can coalesce lifecycle updates before comparing
snapshots, keeping Moiras work away from Athena's stdout/stderr drain path and
making an unchanged running snapshot observable as probable inactivity.

Moiras is imported lazily.  Missing or contract-incompatible packages, invalid
input, and comparison failures become categorical advisory statuses; exception
text never crosses the boundary.
"""

from __future__ import annotations

import importlib
import math
import re
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

_EXPECTED_SCHEMA_VERSION = "1.0"
_COMPATIBLE_PACKAGE_SERIES = (0, 1)
_EXPECTED_SENTINEL_CLASSES = frozenset(
    {
        "REAL_PROGRESS",
        "ACTIVITY_WITHOUT_PROGRESS",
        "PROBABLE_INACTIVITY",
        "LEGITIMATE_WAIT",
        "EXTERNAL_BLOCK",
        "INDETERMINATE",
    }
)
_TERMINAL_STATES = frozenset(
    {
        "COMPLETED",
        "FAILED",
        "CANCELLED",
        "TIMED_OUT",
        "TERMINATION_UNCONFIRMED",
    }
)
_BOUNDARY_KEYS = frozenset(
    {
        "execution_id",
        "attempt_id",
        "state",
        "last_progress_at_monotonic",
        "state_entered_at_monotonic",
        "history",
        "history_length",
        "waiting_for_authorization",
        "waiting_for_credential",
        "external_block",
        "artifact_revision",
    }
)
_BOUNDARY_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,95}\Z")
_SENSITIVE_ID_MARKERS = (
    "token",
    "secret",
    "password",
    "credential",
    "authorization",
    "bearer",
    "api_key",
    "apikey",
    "user",
    "username",
    "path",
    "host",
)
_CREDENTIAL_ID_PREFIXES = (
    "sk-",
    "ghp_",
    "gho_",
    "ghu_",
    "ghs_",
    "ghr_",
    "github_pat_",
    "xox",
    "akia",
    "aiza",
)


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
        if self.classification is not None and self.classification not in _EXPECTED_SENTINEL_CLASSES:
            raise ValueError("unknown Moiras sentinel classification")

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
    artifact_revision: int = 0
    last_progress_marker: float | None = None
    last_state_marker: tuple[str, float | None, int] | None = None


_REQUIRED_MOIRAS_API = (
    "ExecutionSnapshot",
    "LifecycleState",
    "SentinelClass",
    "compare_snapshots",
    "sanitize_value",
    "validate_id",
    "SCHEMA_VERSION",
    "__version__",
)


def _package_series(version: object) -> tuple[int, int] | None:
    if not isinstance(version, str):
        return None
    pieces = version.split(".")
    if len(pieces) < 2 or not pieces[0].isdigit() or not pieces[1].isdigit():
        return None
    return int(pieces[0]), int(pieces[1])


def _enum_values(enum_type: object) -> frozenset[str] | None:
    try:
        return frozenset(str(item.value) for item in enum_type)
    except (TypeError, AttributeError):
        return None


def _validate_boundary_id(value: object) -> str:
    if not isinstance(value, str) or _BOUNDARY_ID_PATTERN.fullmatch(value) is None:
        raise ValueError("invalid boundary identifier")
    lowered = value.casefold()
    if any(marker in lowered for marker in _SENSITIVE_ID_MARKERS):
        raise ValueError("sensitive boundary identifier")
    if lowered.startswith(_CREDENTIAL_ID_PREFIXES):
        raise ValueError("credential-shaped boundary identifier")
    return value


class MoirasShadowObserver:
    """Bounded shadow observer with an optional coalescing sampler.

    ``submit`` is the callback-safe entry point: it copies only allowlisted
    fields and returns without importing or calling Moiras.  When background
    sampling is enabled, one daemon thread evaluates the most recent snapshot
    per attempt at a bounded interval.  Repeated identical RUNNING snapshots do
    not increment activity, so the Sentinel can emit ``PROBABLE_INACTIVITY``.

    Athena never reads an advisory to make a control-flow decision.
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
        background_sampling: bool = False,
        sampling_interval_s: float = 1.0,
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
        if isinstance(background_sampling, bool) is False:
            raise TypeError("background_sampling must be a bool")
        if isinstance(sampling_interval_s, bool) or not isinstance(
            sampling_interval_s, (int, float)
        ):
            raise ValueError("sampling_interval_s must be numeric")
        if not math.isfinite(sampling_interval_s) or sampling_interval_s <= 0:
            raise ValueError("sampling_interval_s must be finite and > 0")

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
        self._attempt_locks: dict[tuple[str, str], threading.Lock] = {}
        self._advisories: OrderedDict[tuple[str, str], MoirasShadowAdvisory] = OrderedDict()
        self._pending: OrderedDict[tuple[str, str], tuple[int, dict[str, Any]]] = OrderedDict()
        self._pending_revision = 0
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
        self._state_lock = threading.RLock()
        self._module_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._sampling_interval_s = float(sampling_interval_s)
        self._worker: threading.Thread | None = None
        if enabled and background_sampling:
            self._worker = threading.Thread(
                target=self._sampling_loop,
                name="athena-moiras-shadow-sampler",
                daemon=True,
            )
            self._worker.start()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def latest(self) -> MoirasShadowAdvisory:
        with self._state_lock:
            return self._latest

    def advisory_for(self, execution_id: str, attempt_id: str) -> MoirasShadowAdvisory | None:
        with self._state_lock:
            return self._advisories.get((execution_id, attempt_id))

    def _load_module(self) -> tuple[ModuleType | None, MoirasAdapterReason | None]:
        with self._module_lock:
            if self._load_attempted:
                if self._module is None:
                    return None, self._load_error or MoirasAdapterReason.PACKAGE_UNAVAILABLE
                return self._module, None
            self._load_attempted = True
            try:
                module = self._module_loader()
            except Exception:
                self._load_error = MoirasAdapterReason.PACKAGE_UNAVAILABLE
                return None, self._load_error

            compatible = all(hasattr(module, name) for name in _REQUIRED_MOIRAS_API)
            compatible = compatible and module.SCHEMA_VERSION == _EXPECTED_SCHEMA_VERSION
            compatible = compatible and (
                _package_series(module.__version__) == _COMPATIBLE_PACKAGE_SERIES
            )
            compatible = compatible and callable(module.compare_snapshots)
            compatible = compatible and callable(module.sanitize_value)
            compatible = compatible and callable(module.validate_id)
            compatible = compatible and (
                _enum_values(module.SentinelClass) == _EXPECTED_SENTINEL_CLASSES
            )
            if not compatible:
                self._load_error = MoirasAdapterReason.INCOMPATIBLE_PACKAGE
                return None, self._load_error
            self._module = module
            self._load_error = None
            return module, None

    def _set_advisory(
        self,
        advisory: MoirasShadowAdvisory,
        key: tuple[str, str] | None = None,
    ) -> MoirasShadowAdvisory:
        with self._state_lock:
            self._latest = advisory
            if key is not None:
                self._advisories[key] = advisory
                self._advisories.move_to_end(key)
                while len(self._advisories) > self._max_attempts:
                    self._advisories.popitem(last=False)
            return advisory

    def _fail(
        self,
        reason: MoirasAdapterReason,
        key: tuple[str, str] | None = None,
    ) -> MoirasShadowAdvisory:
        status = (
            MoirasAdapterStatus.UNAVAILABLE
            if reason
            in {
                MoirasAdapterReason.PACKAGE_UNAVAILABLE,
                MoirasAdapterReason.INCOMPATIBLE_PACKAGE,
            }
            else MoirasAdapterStatus.INDETERMINATE
        )
        return self._set_advisory(
            MoirasShadowAdvisory(status=status, reason=reason),
            key,
        )

    @staticmethod
    def _boundary_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(metadata, Mapping):
            raise ValueError("metadata must be a mapping")
        payload = {key: metadata[key] for key in _BOUNDARY_KEYS if key in metadata}
        if "history" in payload:
            history = payload.pop("history")
            if history is None:
                history = ()
            if not isinstance(history, (list, tuple)):
                raise ValueError("history must be a sequence")
            payload["history_length"] = len(history)
        else:
            payload.setdefault("history_length", 0)
        return payload

    def submit(self, metadata: Mapping[str, Any]) -> MoirasShadowAdvisory:
        """Queue an allowlisted update without importing or calling Moiras."""

        if not self._enabled:
            return self.latest
        try:
            payload = self._boundary_metadata(metadata)
            execution_id = payload["execution_id"]
            attempt_id = payload["attempt_id"]
            _validate_boundary_id(execution_id)
            _validate_boundary_id(attempt_id)
            key = (execution_id, attempt_id)
        except Exception:
            return self._fail(MoirasAdapterReason.INVALID_INPUT)

        if self._worker is None:
            return self.observe(payload)
        with self._state_lock:
            self._pending_revision += 1
            self._pending[key] = (self._pending_revision, payload)
            self._pending.move_to_end(key)
            while len(self._pending) > self._max_attempts:
                self._pending.popitem(last=False)
            return self._advisories.get(key, self._latest)

    def _sampling_loop(self) -> None:
        while not self._stop_event.wait(self._sampling_interval_s):
            with self._state_lock:
                pending = list(self._pending.items())
            for key, (revision, payload) in pending:
                self.observe(payload)
                if payload.get("state") in _TERMINAL_STATES:
                    with self._state_lock:
                        current = self._pending.get(key)
                        if current is not None and current[0] == revision:
                            self._pending.pop(key, None)

    def close(self, timeout_s: float = 2.0) -> None:
        self._stop_event.set()
        worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=max(0.0, timeout_s))

    def observe(self, metadata: Mapping[str, Any]) -> MoirasShadowAdvisory:
        """Synchronously evaluate one allowlisted lifecycle snapshot."""

        if not self._enabled:
            return self.latest
        module, load_error = self._load_module()
        if module is None:
            return self._fail(load_error or MoirasAdapterReason.PACKAGE_UNAVAILABLE)
        try:
            payload = self._boundary_metadata(metadata)
            execution_id = payload["execution_id"]
            attempt_id = payload["attempt_id"]
            state_name = payload["state"]
            _validate_boundary_id(execution_id)
            _validate_boundary_id(attempt_id)
            if not isinstance(state_name, str) or not state_name:
                return self._fail(MoirasAdapterReason.INVALID_INPUT)
            module.validate_id(execution_id, field_name="execution_id")
            module.validate_id(attempt_id, field_name="attempt_id")
            key = (execution_id, attempt_id)
        except (KeyError, TypeError, ValueError):
            return self._fail(MoirasAdapterReason.INVALID_INPUT)

        with self._state_lock:
            attempt_lock = self._attempt_locks.setdefault(key, threading.Lock())
        with attempt_lock:
            try:
                marker = payload.get("last_progress_at_monotonic")
                if marker is not None:
                    if isinstance(marker, bool) or not isinstance(marker, (int, float)):
                        return self._fail(MoirasAdapterReason.INVALID_INPUT, key)
                    if not math.isfinite(marker):
                        return self._fail(MoirasAdapterReason.INVALID_INPUT, key)
                    marker = float(marker)

                state_entered = payload.get("state_entered_at_monotonic")
                if state_entered is not None:
                    if isinstance(state_entered, bool) or not isinstance(
                        state_entered, (int, float)
                    ):
                        return self._fail(MoirasAdapterReason.INVALID_INPUT, key)
                    if not math.isfinite(state_entered):
                        return self._fail(MoirasAdapterReason.INVALID_INPUT, key)
                    state_entered = float(state_entered)

                history_length = payload.get("history_length", 0)
                if isinstance(history_length, bool) or not isinstance(history_length, int):
                    return self._fail(MoirasAdapterReason.INVALID_INPUT, key)

                artifact_revision = payload.get("artifact_revision", 0)
                if (
                    isinstance(artifact_revision, bool)
                    or not isinstance(artifact_revision, int)
                    or artifact_revision < 0
                ):
                    return self._fail(MoirasAdapterReason.INVALID_INPUT, key)

                now_monotonic = self._monotonic_clock()
                if (
                    isinstance(now_monotonic, bool)
                    or not isinstance(now_monotonic, (int, float))
                    or not math.isfinite(now_monotonic)
                ):
                    return self._fail(MoirasAdapterReason.INVALID_INPUT, key)
                monotonic_offset = max(0.0, float(now_monotonic) - self._started_at)
                captured_at = self._utc_now()

                with self._state_lock:
                    attempt = self._attempts.setdefault(key, _AttemptObservation())
                    self._attempts.move_to_end(key)
                    while len(self._attempts) > self._max_attempts:
                        dropped_key, _ = self._attempts.popitem(last=False)
                        self._attempt_locks.pop(dropped_key, None)

                    activity_observed = False
                    if marker is not None and marker != attempt.last_progress_marker:
                        attempt.progress_counter += 1
                        attempt.last_progress_marker = marker
                        activity_observed = True
                    state_marker = (state_name, state_entered, history_length)
                    if (
                        attempt.last_state_marker is not None
                        and state_marker != attempt.last_state_marker
                    ):
                        activity_observed = True
                    attempt.last_state_marker = state_marker
                    if artifact_revision > attempt.artifact_revision:
                        activity_observed = True
                    attempt.artifact_revision = max(
                        attempt.artifact_revision,
                        artifact_revision,
                    )
                    if activity_observed:
                        attempt.activity_counter += 1

                    lifecycle_state = getattr(
                        module.LifecycleState,
                        state_name,
                        module.LifecycleState.UNKNOWN,
                    )
                    snapshot = module.ExecutionSnapshot(
                        execution_id=execution_id,
                        attempt_id=attempt_id,
                        profile="athena-shadow",
                        lifecycle_state=lifecycle_state,
                        captured_at_utc=captured_at,
                        monotonic_offset_s=monotonic_offset,
                        progress_counter=attempt.progress_counter,
                        activity_counter=attempt.activity_counter,
                        artifact_revision=attempt.artifact_revision,
                        waiting_for_authorization=(
                            payload.get("waiting_for_authorization") is True
                        ),
                        waiting_for_credential=(
                            payload.get("waiting_for_credential") is True
                        ),
                        external_block=payload.get("external_block") is True,
                        terminal=state_name in _TERMINAL_STATES,
                    )
                    previous_snapshot = attempt.previous_snapshot

                module.sanitize_value(snapshot.to_dict())
                if previous_snapshot is None:
                    with self._state_lock:
                        attempt.previous_snapshot = snapshot
                    return self._set_advisory(
                        MoirasShadowAdvisory(
                            status=MoirasAdapterStatus.INSUFFICIENT_HISTORY,
                            reason=MoirasAdapterReason.FIRST_SNAPSHOT,
                        ),
                        key,
                    )

                result = module.compare_snapshots(
                    previous_snapshot,
                    snapshot,
                    idle_threshold_s=self._idle_threshold_s,
                )
                module.sanitize_value(result.to_dict())
                classification = result.classification.value
                if classification not in _EXPECTED_SENTINEL_CLASSES:
                    return self._fail(MoirasAdapterReason.INCOMPATIBLE_PACKAGE, key)
                evidence_codes = tuple(code.value for code in result.evidence_codes)
                # Keep the last activity-bearing snapshot as the idle baseline.
                # Advancing it for every sampling tick would compare only one
                # short poll interval at a time, making a longer idle threshold
                # mathematically unreachable.
                if classification not in {"INDETERMINATE", "PROBABLE_INACTIVITY"}:
                    with self._state_lock:
                        attempt.previous_snapshot = snapshot
                return self._set_advisory(
                    MoirasShadowAdvisory(
                        status=MoirasAdapterStatus.OBSERVED,
                        reason=MoirasAdapterReason.CLASSIFICATION_AVAILABLE,
                        classification=classification,
                        evidence_codes=evidence_codes,
                    ),
                    key,
                )
            except Exception:
                return self._fail(MoirasAdapterReason.COMPARISON_REJECTED, key)
