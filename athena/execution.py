"""Execution contract: explicit lifecycle state machine for provider subprocess runs.

Sprint A2 scope: define the state machine, identity/timing/metadata fields, and a
sanitized serialization. This module does not itself manage process groups,
cancellation, or fallback policy — it only models the contract that later sprints
will drive. PROGRESS_OBSERVED is intentionally NOT a state: progress is metadata
(``last_progress_at_*``) attached to the RUNNING state via `record_progress()`.

Sprint A4 scope: deadlines stop being inert metadata. `configure_deadlines()` sets
an absolute ceiling and an idle ceiling for a RUNNING record; `deadline_status()`
classifies (deterministically, from monotonic timestamps only) whether either has
expired so the caller (athena.bridge) knows which one to report. Progress here
means only deterministic, observable signals — new stdout/PTY bytes, a tracked
subprocess completing, an authorized artifact change — never a semantic judgment
about whether the work itself is "going well".
"""

from __future__ import annotations

import math
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class DeadlineBudget:
    """Monotonic countdown budget for multi-stage operations."""

    def __init__(self, total_s: float) -> None:
        if isinstance(total_s, bool) or float(total_s) <= 0 or not math.isfinite(float(total_s)):
            raise ValueError("total_s deve ser número positivo")
        self._total_s = float(total_s)
        self._started_at = time.monotonic()

    @property
    def total_s(self) -> float:
        return self._total_s

    @property
    def elapsed(self) -> float:
        return max(0.0, time.monotonic() - self._started_at)

    @property
    def remaining(self) -> float:
        return max(0.0, self._total_s - self.elapsed)

    @property
    def expired(self) -> bool:
        return self.remaining <= 0.0

    def child_timeout(self, cap: float | None = None) -> float:
        remaining = self.remaining
        if cap is None:
            return remaining
        if isinstance(cap, bool) or float(cap) <= 0 or not math.isfinite(float(cap)):
            raise ValueError("cap inválido")
        return max(0.0, min(float(cap), remaining))


class ExecutionState(str, Enum):
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


TERMINAL_STATES: frozenset[ExecutionState] = frozenset(
    {
        ExecutionState.COMPLETED,
        ExecutionState.FAILED,
        ExecutionState.CANCELLED,
        ExecutionState.TIMED_OUT,
        ExecutionState.TERMINATION_UNCONFIRMED,
    }
)

# Explicit transition table. Terminal states map to an empty set (no further
# transitions are ever valid). Races between "it finished" and "it was being
# cancelled/killed" are modeled by allowing COMPLETED from the in-flight
# cancellation states too — the outcome that actually happened wins.
VALID_TRANSITIONS: dict[ExecutionState, frozenset[ExecutionState]] = {
    ExecutionState.QUEUED: frozenset(
        {ExecutionState.STARTING, ExecutionState.CANCELLED, ExecutionState.FAILED}
    ),
    ExecutionState.STARTING: frozenset(
        {
            ExecutionState.RUNNING,
            ExecutionState.FAILED,
            ExecutionState.CANCELLED,
            ExecutionState.TIMED_OUT,
        }
    ),
    ExecutionState.RUNNING: frozenset(
        {
            ExecutionState.CANCELLATION_REQUESTED,
            ExecutionState.TERMINATING,
            ExecutionState.COMPLETED,
            ExecutionState.FAILED,
            ExecutionState.TIMED_OUT,
        }
    ),
    ExecutionState.CANCELLATION_REQUESTED: frozenset(
        {
            ExecutionState.TERMINATING,
            ExecutionState.CANCELLED,
            ExecutionState.COMPLETED,
            ExecutionState.FAILED,
            ExecutionState.TIMED_OUT,
        }
    ),
    ExecutionState.TERMINATING: frozenset(
        {
            ExecutionState.CANCELLED,
            ExecutionState.TIMED_OUT,
            ExecutionState.TERMINATION_UNCONFIRMED,
            ExecutionState.FAILED,
            ExecutionState.COMPLETED,
        }
    ),
    ExecutionState.COMPLETED: frozenset(),
    ExecutionState.FAILED: frozenset(),
    ExecutionState.CANCELLED: frozenset(),
    ExecutionState.TIMED_OUT: frozenset(),
    ExecutionState.TERMINATION_UNCONFIRMED: frozenset(),
}


class InvalidTransitionError(Exception):
    """Raised when an ExecutionRecord transition is not allowed from its current state."""

    def __init__(self, current: ExecutionState, target: ExecutionState) -> None:
        self.current = current
        self.target = target
        super().__init__(f"Invalid execution state transition: {current.value} -> {target.value}")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


SAFE_CANCEL_REASONS: frozenset[str] = frozenset(
    {
        "user_requested",
        "client_abandoned",
        "server_shutdown",
        "control_plane",
    }
)


def normalize_cancel_reason(reason: str | None) -> str:
    if not reason:
        return "user_requested"
    candidate = str(reason).strip().lower().replace(" ", "_")
    if candidate in SAFE_CANCEL_REASONS:
        return candidate
    return "user_requested"


class ExecutionControl:
    """Thread-safe cancellation control shared by all attempts of one execution."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._cancel_reason = "user_requested"
        self._client_abandoned = False

    @property
    def cancellation_requested(self) -> bool:
        return self._cancel_event.is_set()

    @property
    def cancel_reason(self) -> str:
        with self._lock:
            return self._cancel_reason

    @property
    def client_abandoned(self) -> bool:
        with self._lock:
            return self._client_abandoned

    def request_cancel(
        self,
        *,
        reason: str | None = None,
        client_abandoned: bool = False,
    ) -> bool:
        normalized_reason = normalize_cancel_reason(reason)
        with self._lock:
            if client_abandoned:
                self._client_abandoned = True
                normalized_reason = "client_abandoned"
            if not self._cancel_event.is_set():
                self._cancel_reason = normalized_reason
                self._cancel_event.set()
                return True
            # Idempotent, but preserve the stronger abandoned marker forever.
            if self._client_abandoned:
                self._cancel_reason = "client_abandoned"
            return False


@dataclass
class StateTransition:
    from_state: ExecutionState | None
    to_state: ExecutionState
    at_utc: str
    at_monotonic: float
    reason: str | None = None


@dataclass
class ExecutionRecord:
    """Tracks the lifecycle of a single provider CLI execution attempt.

    Identity: `execution_id` scopes one logical run request; `attempt_id` scopes
    one concrete attempt at it (a fallback/retry gets a new attempt_id under the
    same execution_id). Both default to fresh UUIDs so callers may omit them.
    """

    provider: str
    profile: str | None = None
    transport: str = "local"
    execution_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    attempt_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    state: ExecutionState = ExecutionState.QUEUED

    created_at_utc: str = field(default_factory=_utc_now_iso)
    created_at_monotonic: float = field(default_factory=time.monotonic)
    state_entered_at_utc: str = field(default_factory=_utc_now_iso)
    state_entered_at_monotonic: float = field(default_factory=time.monotonic)

    # Absolute ceiling on total run time, and idle ceiling on time since the
    # last observed progress signal. Both are measured from monotonic
    # timestamps only (created_at_monotonic / last_progress_at_monotonic /
    # state_entered_at_monotonic) so classification in `deadline_status()` is
    # deterministic and immune to wall-clock adjustments.
    absolute_deadline_s: float | None = None
    idle_deadline_s: float | None = None

    # Grace window the caller should honor between SIGTERM and SIGKILL when
    # tearing down a process for this record. Metadata only: athena.bridge
    # owns actually signaling processes; this just carries the configured
    # value alongside the rest of the record so it is visible in to_dict().
    termination_grace_s: float | None = None

    last_progress_at_utc: str | None = None
    last_progress_at_monotonic: float | None = None

    pid: int | None = None
    pgid: int | None = None

    # Whether a real OS process was ever spawned for this attempt. False for
    # the whole lifetime of a record whose Popen() call raised
    # FileNotFoundError/OSError before any process existed -- there is
    # nothing to confirm terminated in that case. Set True exactly when
    # `set_process_identity()` is called, i.e. right after Popen() succeeds.
    process_created: bool = False
    remote_session_started: bool = False
    remote_termination_confirmed: bool | None = None

    termination_reason: str | None = None
    direct_process_terminated_confirmed: bool = False
    process_tree_terminated_confirmed: bool = False

    client_abandoned: bool = False
    fallback_started: bool = False

    history: list[StateTransition] = field(default_factory=list)
    on_update: Callable[[dict], None] | None = field(default=None, repr=False, compare=False)

    def _emit_update(self) -> None:
        if self.on_update is None:
            return
        try:
            self.on_update(self.to_dict())
        except Exception:
            return

    def __post_init__(self) -> None:
        if self.transport not in {"local", "ssh"}:
            raise ValueError("transport deve ser 'local' ou 'ssh'")
        self.history.append(
            StateTransition(
                from_state=None,
                to_state=self.state,
                at_utc=self.created_at_utc,
                at_monotonic=self.created_at_monotonic,
            )
        )
        self._emit_update()

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def transition(self, target: ExecutionState, *, reason: str | None = None) -> None:
        """Move to `target`, raising InvalidTransitionError if not allowed.

        No-op self-transitions are rejected too (including from a terminal
        state) so terminal states stay unambiguous: once reached, the record
        never changes state again.
        """
        allowed = VALID_TRANSITIONS.get(self.state, frozenset())
        if target not in allowed:
            raise InvalidTransitionError(self.state, target)
        self.state = target
        self.state_entered_at_utc = _utc_now_iso()
        self.state_entered_at_monotonic = time.monotonic()
        if reason is not None:
            self.termination_reason = reason
        self.history.append(
            StateTransition(
                from_state=self.history[-1].to_state,
                to_state=target,
                at_utc=self.state_entered_at_utc,
                at_monotonic=self.state_entered_at_monotonic,
                reason=reason,
            )
        )
        self._emit_update()

    def configure_deadlines(
        self,
        *,
        absolute_deadline_s: float | None = None,
        idle_deadline_s: float | None = None,
        termination_grace_s: float | None = None,
    ) -> None:
        """Set the deterministic timing budget for this record.

        Callers pass whatever they were configured with; `None` for either
        deadline means "no ceiling of that kind" (matches pre-A4 behavior).
        """
        self.absolute_deadline_s = absolute_deadline_s
        self.idle_deadline_s = idle_deadline_s
        self.termination_grace_s = termination_grace_s
        self._emit_update()

    def deadline_status(self, now_monotonic: float | None = None) -> str | None:
        """Classify which deadline (if any) has expired, as of `now_monotonic`.

        Returns "absolute_deadline", "idle_deadline", or None. The absolute
        ceiling is checked first and always wins when both are expired at
        once -- it is a hard cap that must be enforced even while progress
        (e.g. a heartbeat) keeps resetting the idle clock. The absolute
        deadline is measured from `created_at_monotonic` (process start);
        the idle deadline is measured from the last observed progress, or
        from the moment RUNNING was entered if no progress has been observed
        yet.
        """
        if now_monotonic is None:
            now_monotonic = time.monotonic()

        if self.absolute_deadline_s is not None:
            if now_monotonic - self.created_at_monotonic >= self.absolute_deadline_s:
                return "absolute_deadline"

        if self.idle_deadline_s is not None:
            idle_since = self.last_progress_at_monotonic or self.state_entered_at_monotonic
            if now_monotonic - idle_since >= self.idle_deadline_s:
                return "idle_deadline"

        return None

    def record_progress(self) -> None:
        """Record a PROGRESS_OBSERVED event: metadata on RUNNING, not a state.

        Only meaningful while RUNNING; calling it in any other state is a
        no-op guard against stray progress signals racing a state change.
        """
        if self.state is not ExecutionState.RUNNING:
            return
        self.last_progress_at_utc = _utc_now_iso()
        self.last_progress_at_monotonic = time.monotonic()
        self._emit_update()

    def set_process_identity(self, pid: int | None, pgid: int | None = None) -> None:
        self.pid = pid
        self.pgid = pgid
        self.process_created = True
        self._emit_update()

    def confirm_direct_process_terminated(self) -> None:
        self.direct_process_terminated_confirmed = True
        self._emit_update()

    def confirm_process_tree_terminated(self) -> None:
        self.process_tree_terminated_confirmed = True
        self._emit_update()

    def mark_remote_session_started(self) -> None:
        self.remote_session_started = True
        # Unknown until an explicit remote-side confirmation mechanism exists.
        if self.remote_termination_confirmed is None:
            self.remote_termination_confirmed = False
        self._emit_update()

    def confirm_remote_termination(self) -> None:
        self.remote_termination_confirmed = True
        self._emit_update()

    def mark_client_abandoned(self) -> None:
        self.client_abandoned = True
        if self.termination_reason is None:
            self.termination_reason = "client_abandoned"
        self._emit_update()

    def mark_fallback_started(self) -> None:
        self.fallback_started = True
        self._emit_update()

    def to_dict(self) -> dict:
        """Sanitized serialization: identity, timing, and lifecycle metadata only.

        Deliberately excludes prompt, response, full command, and any
        credential/secret material — those never enter this record.
        """
        return {
            "execution_id": self.execution_id,
            "attempt_id": self.attempt_id,
            "provider": self.provider,
            "profile": self.profile,
            "transport": self.transport,
            "state": self.state.value,
            "is_terminal": self.is_terminal,
            "created_at_utc": self.created_at_utc,
            "created_at_monotonic": self.created_at_monotonic,
            "state_entered_at_utc": self.state_entered_at_utc,
            "state_entered_at_monotonic": self.state_entered_at_monotonic,
            "absolute_deadline_s": self.absolute_deadline_s,
            "idle_deadline_s": self.idle_deadline_s,
            "termination_grace_s": self.termination_grace_s,
            "last_progress_at_utc": self.last_progress_at_utc,
            "last_progress_at_monotonic": self.last_progress_at_monotonic,
            "pid": self.pid,
            "pgid": self.pgid,
            "process_created": self.process_created,
            "remote_session_started": self.remote_session_started,
            "remote_termination_confirmed": self.remote_termination_confirmed,
            "termination_reason": self.termination_reason,
            "direct_process_terminated_confirmed": self.direct_process_terminated_confirmed,
            "process_tree_terminated_confirmed": self.process_tree_terminated_confirmed,
            "client_abandoned": self.client_abandoned,
            "fallback_started": self.fallback_started,
            "history": [
                {
                    "from_state": t.from_state.value if t.from_state else None,
                    "to_state": t.to_state.value,
                    "at_utc": t.at_utc,
                    "at_monotonic": t.at_monotonic,
                    "reason": t.reason,
                }
                for t in self.history
            ],
        }
