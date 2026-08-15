from __future__ import annotations

import hashlib
import math
import re
import threading
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from athena.execution import ExecutionControl, normalize_cancel_reason

TERMINAL_REGISTRY_STATES = frozenset(
    {
        "COMPLETED",
        "FAILED",
        "FAILED_PRELAUNCH",
        "CANCELLED",
        "TIMED_OUT",
        "TERMINATION_UNCONFIRMED",
        "RESULT_INDETERMINATE",
    }
)

SAFE_REASON_CODES = frozenset(
    {
        "client_abandoned",
        "user_requested",
        "absolute_deadline",
        "idle_deadline",
        "verification_deadline",
        "combo_overall_deadline",
        "command_not_found",
        "natural_exit_cleanup",
        "termination_unconfirmed",
        "internal_error",
        "other_redacted",
    }
)

_LIFECYCLE_ALLOWED_FIELDS = frozenset(
    {
        "execution_id",
        "attempt_id",
        "provider",
        "profile",
        "transport",
        "state",
        "is_terminal",
        "created_at_utc",
        "created_at_monotonic",
        "state_entered_at_utc",
        "state_entered_at_monotonic",
        "absolute_deadline_s",
        "idle_deadline_s",
        "termination_grace_s",
        "last_progress_at_utc",
        "last_progress_at_monotonic",
        "pid",
        "pgid",
        "process_created",
        "remote_session_started",
        "remote_termination_confirmed",
        "termination_reason",
        "direct_process_terminated_confirmed",
        "process_tree_terminated_confirmed",
        "client_abandoned",
        "fallback_started",
        "history",
    }
)

_SNAPSHOT_STATES = frozenset(
    {
        "QUEUED",
        "STARTING",
        "RUNNING",
        "CANCELLATION_REQUESTED",
        "TERMINATING",
        "COMPLETED",
        "FAILED",
        "FAILED_PRELAUNCH",
        "CANCELLED",
        "TIMED_OUT",
        "TERMINATION_UNCONFIRMED",
        "RESULT_INDETERMINATE",
    }
)

_TIMESTAMP_FIELDS = frozenset(
    {
        "created_at_utc",
        "state_entered_at_utc",
        "last_progress_at_utc",
    }
)
_MONOTONIC_FIELDS = frozenset(
    {
        "created_at_monotonic",
        "state_entered_at_monotonic",
        "last_progress_at_monotonic",
        "absolute_deadline_s",
        "idle_deadline_s",
        "termination_grace_s",
    }
)
_BOOL_FIELDS = frozenset(
    {
        "process_created",
        "remote_session_started",
        "remote_termination_confirmed",
        "direct_process_terminated_confirmed",
        "process_tree_terminated_confirmed",
        "client_abandoned",
        "fallback_started",
    }
)
_INT_FIELDS = frozenset({"pid", "pgid"})


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_identifier(value: Any, *, field_name: str, max_len: int = 96) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} inválido: deve ser string")
    candidate = value.strip()
    if not candidate:
        raise ValueError(f"{field_name} inválido: vazio")
    if len(candidate) > max_len:
        raise ValueError(f"{field_name} inválido: excede {max_len} caracteres")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", candidate) is None:
        raise ValueError(f"{field_name} inválido: caracteres não permitidos")
    return candidate


def _normalize_identifier(value: Any, *, max_len: int = 64) -> str | None:
    if value is None:
        return None
    raw = str(value).strip().lower()
    if not raw:
        return None
    normalized = re.sub(r"[^a-z0-9_-]+", "_", raw)
    normalized = re.sub(r"_+", "_", normalized).strip("._-")
    if not normalized:
        return None
    return normalized[:max_len]


_IDENTIFIER_TOKEN_MARKERS = (
    "token",
    "secret",
    "password",
    "credential",
    "authorization",
    "bearer",
    "api_key",
    "apikey",
)
_COMMON_TOKEN_PREFIXES = ("sk-", "ghp_", "gho_", "xoxb-", "xoxp-", "ya29.")


def _looks_high_entropy(text: str) -> bool:
    if len(text) < 20:
        return False
    if re.fullmatch(r"[A-Za-z0-9._~-]+", text) is None:
        return False
    unique_ratio = len(set(text)) / len(text)
    has_alpha = any(ch.isalpha() for ch in text)
    has_digit = any(ch.isdigit() for ch in text)
    return unique_ratio >= 0.55 and has_alpha and has_digit


def _contains_sensitive_markers(text: str) -> bool:
    lowered = text.lower()
    if any(marker in lowered for marker in _IDENTIFIER_TOKEN_MARKERS):
        return True
    if any(prefix in lowered for prefix in _COMMON_TOKEN_PREFIXES):
        return True
    return False


def _is_short_id_piece(piece: str) -> bool:
    return re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,31}", piece) is not None


def _sanitize_allowed_identifier(
    value: Any,
    *,
    fallback: str = "redacted_identifier",
    max_len: int = 64,
    allow_slash_pair: bool = False,
) -> str:
    raw = str(value or "").strip()
    if not raw:
        return fallback
    if len(raw) > max_len:
        return fallback
    lowered = raw.lower()
    if _contains_sensitive_markers(lowered):
        return fallback
    if any(ch.isspace() for ch in lowered):
        return fallback
    if "\\" in lowered:
        return fallback
    if "/" in lowered:
        if not allow_slash_pair:
            return fallback
        parts = lowered.split("/")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            return fallback
        if not (_is_short_id_piece(parts[0]) and _is_short_id_piece(parts[1])):
            return fallback
        return f"{parts[0]}/{parts[1]}"
    if re.fullmatch(r"[a-z0-9][a-z0-9._-]*", lowered) is None:
        return fallback
    if _looks_high_entropy(lowered):
        return fallback
    return lowered


def _sanitize_transport_identifier(value: Any) -> str:
    normalized = _sanitize_allowed_identifier(value, fallback="unknown", max_len=16)
    if normalized not in {"local", "ssh"}:
        return "unknown"
    return normalized


def _public_request_id(value: Any) -> int | str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return "redacted_identifier"
        if (
            len(candidate) <= 96
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", candidate) is not None
            and not _contains_sensitive_markers(candidate.lower())
            and not _looks_high_entropy(candidate)
        ):
            return candidate
        digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
        return f"sha256:{digest[:24]}"
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    return f"sha256:{digest[:24]}"


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            sanitized[str(key)] = _json_safe(item)
        return sanitized
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, set):
        sanitized_items = [_json_safe(item) for item in value]
        return sorted(sanitized_items, key=repr)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _sanitize_iso_timestamp(value: Any, *, max_len: int = 64) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        candidate = value.strip()
    elif isinstance(value, datetime):
        candidate = value.isoformat()
    else:
        return None
    if not candidate:
        return None
    if len(candidate) > max_len:
        candidate = candidate[:max_len]
    if (
        re.fullmatch(r"\d{4}-\d{2}-\d{2}T[0-9:.+-Zz]+", candidate) is None
        and re.fullmatch(r"\d{4}-\d{2}-\d{2} [0-9:.+-Zz]+", candidate) is None
    ):
        return None
    return candidate


def _sanitize_finite_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        candidate = float(value)
        if not math.isfinite(candidate):
            return None
        return candidate
    return None


def _sanitize_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    return None


def _categorize_reason(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text in SAFE_REASON_CODES:
        return text
    if "client_abandoned" in text:
        return "client_abandoned"
    if "user_requested" in text or "server_shutdown" in text or "control_plane" in text:
        return "user_requested"
    if "verification_deadline" in text:
        return "verification_deadline"
    if "combo_overall_deadline" in text or "overall_timeout" in text:
        return "combo_overall_deadline"
    if "idle_deadline" in text or "inatividade" in text:
        return "idle_deadline"
    if "absolute_deadline" in text or ("timeout" in text and "idle" not in text and "combo" not in text):
        return "absolute_deadline"
    if "command_not_found" in text or "filenotfound" in text or "not found" in text:
        return "command_not_found"
    if "natural_exit_cleanup" in text:
        return "natural_exit_cleanup"
    if "termination_unconfirmed" in text:
        return "termination_unconfirmed"
    if "internal" in text or "error" in text or "exception" in text:
        return "internal_error"
    return "other_redacted"


def _sanitize_history(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value[:32]:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "from_state": _sanitize_state(item.get("from_state"), allow_none=True),
                "to_state": _sanitize_state(item.get("to_state"), allow_none=True),
                "at_utc": _sanitize_iso_timestamp(item.get("at_utc")),
                "at_monotonic": _sanitize_finite_number(item.get("at_monotonic")),
                "reason": _categorize_reason(item.get("reason")),
            }
        )
    return out


def _sanitize_state(value: Any, *, allow_none: bool = False) -> str | None:
    if value is None:
        if allow_none:
            return None
        raise ValueError("state ausente")
    if not isinstance(value, str):
        raise ValueError("state inválido: deve ser string")
    candidate = value.strip().upper()
    if candidate not in _SNAPSHOT_STATES:
        raise ValueError(f"state inválido: {value}")
    return candidate


def _sanitize_attempt_snapshot(execution_id: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    snapshot_execution_id = _sanitize_identifier(
        snapshot.get("execution_id"), field_name="execution_id", max_len=96
    )
    expected_execution_id = _sanitize_identifier(
        execution_id, field_name="execution_id", max_len=96
    )
    if snapshot_execution_id != expected_execution_id:
        raise ValueError(
            f"execution_id divergente no snapshot: esperado={execution_id} recebido={snapshot.get('execution_id')}"
        )
    attempt_id = _sanitize_identifier(snapshot.get("attempt_id"), field_name="attempt_id")
    sanitized: dict[str, Any] = {
        "execution_id": expected_execution_id,
        "attempt_id": attempt_id,
    }
    for key in _LIFECYCLE_ALLOWED_FIELDS:
        if key in {"execution_id", "attempt_id"}:
            continue
        if key not in snapshot:
            continue
        if key == "state":
            sanitized["state"] = _sanitize_state(snapshot.get("state"))
            continue
        if key == "is_terminal":
            continue
        if key == "history":
            sanitized["history"] = _sanitize_history(snapshot.get("history"))
            continue
        if key == "termination_reason":
            sanitized["termination_reason"] = _categorize_reason(snapshot.get("termination_reason"))
            continue
        if key in _TIMESTAMP_FIELDS:
            sanitized[key] = _sanitize_iso_timestamp(snapshot.get(key))
            continue
        if key in _MONOTONIC_FIELDS:
            sanitized[key] = _sanitize_finite_number(snapshot.get(key))
            continue
        if key in _BOOL_FIELDS:
            value = snapshot.get(key)
            sanitized[key] = value if isinstance(value, bool) else None
            continue
        if key in _INT_FIELDS:
            sanitized[key] = _sanitize_int(snapshot.get(key))
            continue
        sanitized[key] = _json_safe(snapshot.get(key))
    sanitized["is_terminal"] = sanitized.get("state") in TERMINAL_REGISTRY_STATES
    sanitized["provider"] = _sanitize_allowed_identifier(
        sanitized.get("provider"),
        fallback="redacted_identifier",
        allow_slash_pair=True,
    )
    sanitized["profile"] = _sanitize_allowed_identifier(
        sanitized.get("profile"),
        fallback="redacted_identifier",
        allow_slash_pair=True,
    )
    sanitized["transport"] = _sanitize_transport_identifier(sanitized.get("transport"))
    return sanitized


def _validate_positive_int(value: int, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} deve ser inteiro positivo (bool não permitido)")
    return value


@dataclass
class ExecutionRegistryRecord:
    execution_id: str
    request_id: Any
    tool: str
    state: str = "QUEUED"
    current_attempt_id: str | None = None
    attempts: dict[str, dict[str, Any]] = field(default_factory=dict)
    attempt_order: list[str] = field(default_factory=list)
    attempts_truncated: int = 0
    finalized: bool = False
    client_abandoned: bool = False
    created_at_utc: str = field(default_factory=_utc_now_iso)
    updated_at_utc: str = field(default_factory=_utc_now_iso)
    on_update: Callable[[dict[str, Any]], None] | None = field(default=None, repr=False, compare=False)
    control: ExecutionControl | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        public_request_id = _public_request_id(self.request_id)
        return {
            "execution_id": self.execution_id,
            "request_id": public_request_id,
            "public_request_id": public_request_id,
            "tool": self.tool,
            "state": self.state,
            "current_attempt_id": self.current_attempt_id,
            "attempt_order": list(self.attempt_order),
            "attempts": deepcopy(self.attempts),
            "attempts_truncated": self.attempts_truncated,
            "finalized": self.finalized,
            "client_abandoned": self.client_abandoned,
            "created_at_utc": self.created_at_utc,
            "updated_at_utc": self.updated_at_utc,
        }


class ExecutionRegistry:
    def __init__(self, *, max_records: int = 256, max_attempts_per_record: int = 64) -> None:
        self._max_records = _validate_positive_int(max_records, name="max_records")
        self._max_attempts_per_record = _validate_positive_int(
            max_attempts_per_record, name="max_attempts_per_record"
        )
        self._lock = threading.Lock()
        self._records_by_execution_id: dict[str, ExecutionRegistryRecord] = {}
        self._execution_by_request_id: dict[Any, str] = {}
        self._record_order: list[str] = []

    def _emit(
        self,
        on_update: Callable[[dict[str, Any]], None] | None,
        payload: dict[str, Any],
    ) -> None:
        if on_update is None:
            return
        try:
            on_update(deepcopy(payload))
        except Exception:
            # Registry updates must remain best-effort for observers.
            return

    def _evict_oldest_finalized_until_space(self) -> None:
        while len(self._records_by_execution_id) >= self._max_records:
            evicted = False
            for execution_id in list(self._record_order):
                record = self._records_by_execution_id.get(execution_id)
                if record is None:
                    self._record_order.remove(execution_id)
                    continue
                if not record.finalized:
                    continue
                del self._records_by_execution_id[execution_id]
                self._execution_by_request_id.pop(record.request_id, None)
                self._record_order.remove(execution_id)
                evicted = True
                break
            if not evicted:
                raise ValueError(
                    "ExecutionRegistry lotado com execuções ativas; finalize execuções antes de criar novas."
                )

    def create(
        self,
        *,
        execution_id: str,
        request_id: Any,
        tool: str,
        control: ExecutionControl | None = None,
        on_update: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        sanitized_execution_id = _sanitize_identifier(
            execution_id,
            field_name="execution_id",
            max_len=96,
        )
        sanitized_tool = _sanitize_identifier(tool, field_name="tool", max_len=64)
        with self._lock:
            if sanitized_execution_id in self._records_by_execution_id:
                raise ValueError(f"execution_id duplicado: {execution_id}")
            if request_id in self._execution_by_request_id:
                raise ValueError(f"request_id duplicado: {request_id}")
            self._evict_oldest_finalized_until_space()
            record = ExecutionRegistryRecord(
                execution_id=sanitized_execution_id,
                request_id=request_id,
                tool=sanitized_tool,
                on_update=on_update,
                control=control,
            )
            self._records_by_execution_id[sanitized_execution_id] = record
            self._execution_by_request_id[request_id] = sanitized_execution_id
            self._record_order.append(sanitized_execution_id)
            payload = record.to_dict()
            callback = record.on_update
        self._emit(callback, payload)
        return deepcopy(payload)

    def update_attempt(self, execution_id: str, attempt_snapshot: dict[str, Any]) -> dict[str, Any] | None:
        sanitized = _sanitize_attempt_snapshot(execution_id, attempt_snapshot)
        with self._lock:
            record = self._records_by_execution_id.get(execution_id)
            if record is None:
                return None
            if record.finalized:
                return deepcopy(record.to_dict())
            attempt_id = str(sanitized["attempt_id"])
            existing = record.attempts.get(attempt_id)
            if existing is not None and existing.get("state") in TERMINAL_REGISTRY_STATES:
                return deepcopy(record.to_dict())
            is_new_attempt = attempt_id not in record.attempt_order
            if is_new_attempt:
                record.attempt_order.append(attempt_id)
            record.attempts[attempt_id] = deepcopy(sanitized)
            if is_new_attempt:
                record.current_attempt_id = attempt_id
            if record.current_attempt_id == attempt_id:
                state = sanitized.get("state")
                if isinstance(state, str):
                    record.state = state
            if is_new_attempt:
                while len(record.attempt_order) > self._max_attempts_per_record:
                    candidate_to_drop: str | None = None
                    for candidate in record.attempt_order:
                        if candidate != record.current_attempt_id:
                            candidate_to_drop = candidate
                            break
                    if candidate_to_drop is None:
                        break
                    record.attempt_order.remove(candidate_to_drop)
                    record.attempts.pop(candidate_to_drop, None)
                    record.attempts_truncated += 1
            # Sticky flag: once True, snapshots must never revert it to False.
            record.client_abandoned = record.client_abandoned or bool(
                sanitized.get("client_abandoned", False)
            )
            record.updated_at_utc = _utc_now_iso()
            payload = record.to_dict()
            callback = record.on_update
        self._emit(callback, payload)
        return deepcopy(payload)

    def mark_state(self, execution_id: str, state: str) -> dict[str, Any] | None:
        next_state = _sanitize_state(state)
        with self._lock:
            record = self._records_by_execution_id.get(execution_id)
            if record is None:
                return None
            if record.finalized:
                return deepcopy(record.to_dict())
            record.state = next_state
            record.updated_at_utc = _utc_now_iso()
            payload = record.to_dict()
            callback = record.on_update
        self._emit(callback, payload)
        return deepcopy(payload)

    def mark_state_if_not_in(
        self,
        execution_id: str,
        state: str,
        excluded_states: set[str] | list[str] | tuple[str, ...],
    ) -> dict[str, Any] | None:
        next_state = _sanitize_state(state)
        excluded = {_sanitize_state(item) for item in excluded_states}
        with self._lock:
            record = self._records_by_execution_id.get(execution_id)
            if record is None:
                return None
            if record.finalized:
                return deepcopy(record.to_dict())
            if record.state in excluded:
                return deepcopy(record.to_dict())
            record.state = next_state
            record.updated_at_utc = _utc_now_iso()
            payload = record.to_dict()
            callback = record.on_update
        self._emit(callback, payload)
        return deepcopy(payload)

    def get(
        self,
        *,
        execution_id: str | None = None,
        request_id: Any | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            resolved_execution_id = execution_id
            if resolved_execution_id is None and request_id is not None:
                resolved_execution_id = self._execution_by_request_id.get(request_id)
            if resolved_execution_id is None:
                return None
            record = self._records_by_execution_id.get(resolved_execution_id)
            if record is None:
                return None
            return deepcopy(record.to_dict())

    def list(self, *, limit: int = 20) -> list[dict[str, Any]]:
        if isinstance(limit, bool):
            safe_limit = 0
        else:
            try:
                safe_limit = int(limit)
            except (TypeError, ValueError):
                safe_limit = 0
        safe_limit = max(0, min(safe_limit, 100))
        with self._lock:
            records = sorted(
                self._records_by_execution_id.values(),
                key=lambda item: item.updated_at_utc,
                reverse=True,
            )
            return [deepcopy(record.to_dict()) for record in records[:safe_limit]]

    def finalize(self, execution_id: str, state: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            record = self._records_by_execution_id.get(execution_id)
            if record is None:
                return None
            if record.finalized:
                return deepcopy(record.to_dict())
            final_state = state
            if final_state is not None and final_state not in TERMINAL_REGISTRY_STATES:
                raise ValueError(f"state inválido para finalize: {final_state}")
            if final_state is None:
                final_state = (
                    record.state
                    if record.state in TERMINAL_REGISTRY_STATES
                    else "RESULT_INDETERMINATE"
                )
            record.state = final_state
            record.finalized = True
            record.updated_at_utc = _utc_now_iso()
            payload = record.to_dict()
            callback = record.on_update
        self._emit(callback, payload)
        return deepcopy(payload)

    def request_cancel(
        self,
        *,
        execution_id: str | None = None,
        request_id: Any | None = None,
        reason: str | None = None,
        client_abandoned: bool = False,
    ) -> dict[str, Any]:
        normalized_reason = normalize_cancel_reason(reason)
        control: ExecutionControl | None = None
        payload: dict[str, Any] | None = None
        callback: Callable[[dict[str, Any]], None] | None = None
        requested = False
        found = False
        resolved_execution_id: str | None = None

        with self._lock:
            resolved_execution_id = execution_id
            if resolved_execution_id is None and request_id is not None:
                resolved_execution_id = self._execution_by_request_id.get(request_id)
            if resolved_execution_id is None:
                return {
                    "found": False,
                    "requested": False,
                    "execution_id": None,
                    "reason": normalized_reason,
                }
            record = self._records_by_execution_id.get(resolved_execution_id)
            if record is None:
                return {
                    "found": False,
                    "requested": False,
                    "execution_id": resolved_execution_id,
                    "reason": normalized_reason,
                }
            found = True
            if record.finalized:
                return {
                    "found": True,
                    "requested": False,
                    "execution_id": resolved_execution_id,
                    "reason": normalized_reason,
                }
            if client_abandoned:
                record.client_abandoned = True
                normalized_reason = "client_abandoned"
            record.updated_at_utc = _utc_now_iso()
            payload = record.to_dict()
            callback = record.on_update
            control = record.control

        if control is not None:
            requested = control.request_cancel(
                reason=normalized_reason,
                client_abandoned=client_abandoned,
            )
        if payload is not None:
            self._emit(callback, payload)
        return {
            "found": found,
            "requested": requested,
            "execution_id": resolved_execution_id,
            "reason": normalized_reason,
        }

    def abandon_all_nonterminal(self, *, reason: str | None = None) -> dict[str, Any]:
        normalized_reason = normalize_cancel_reason(reason)
        controls: list[ExecutionControl] = []
        updates: list[tuple[Callable[[dict[str, Any]], None] | None, dict[str, Any]]] = []
        with self._lock:
            for record in self._records_by_execution_id.values():
                if record.finalized:
                    continue
                record.client_abandoned = True
                record.updated_at_utc = _utc_now_iso()
                updates.append((record.on_update, record.to_dict()))
                if record.control is not None:
                    controls.append(record.control)
        requested = 0
        for control in controls:
            if control.request_cancel(reason=normalized_reason, client_abandoned=True):
                requested += 1
        for callback, payload in updates:
            self._emit(callback, payload)
        return {"affected": len(updates), "requested": requested}
