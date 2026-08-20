"""Implementação em memória do registro limitado de execuções."""

from __future__ import annotations

import copy
import hashlib
import re
import threading
from collections import OrderedDict
from collections.abc import Mapping
from typing import Any

from athena.execution import ExecutionControl

from .contracts import AttemptSnapshot, RegistryEntry, RequestId

DEFAULT_MAX_EXECUTIONS = 256
DEFAULT_MAX_ATTEMPTS_PER_EXECUTION = 64

_ATTEMPT_FIELDS = frozenset(
    {
        "absolute_deadline_s",
        "attempt_id",
        "duration_ms",
        "exit_code",
        "finished_at",
        "idle_deadline_s",
        "profile",
        "provider",
        "reason",
        "started_at",
        "state",
        "transport",
    }
)
_SENSITIVE_REQUEST_HINT = re.compile(
    r"(?:bearer|credential|password|secret|token)", re.IGNORECASE
)
_TERMINAL_STATES = frozenset(
    {
        "cancelled",
        "completed",
        "failed",
        "termination_unconfirmed",
        "timed_out",
    }
)


def _validate_capacity(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _validate_request_id(request_id: RequestId) -> None:
    if isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
        raise TypeError("request_id must be a string or integer (bool is not allowed)")
    if isinstance(request_id, str) and not request_id:
        raise ValueError("request_id must not be empty")


def _request_key(request_id: RequestId) -> str:
    kind = "integer" if isinstance(request_id, int) else "string"
    encoded = f"{kind}:{request_id}".encode()
    return hashlib.sha256(encoded).hexdigest()


def _public_request_id(request_id: RequestId) -> RequestId | str:
    if isinstance(request_id, int):
        return request_id
    if len(request_id) < 24 and _SENSITIVE_REQUEST_HINT.search(request_id) is None:
        return request_id
    digest = hashlib.sha256(request_id.encode()).hexdigest()
    return f"sha256:{digest}"


def _sanitize_attempt(snapshot: AttemptSnapshot) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for field in _ATTEMPT_FIELDS:
        value = snapshot.get(field)
        if field in snapshot and (
            value is None or isinstance(value, (bool, int, float, str))
        ):
            sanitized[field] = value
    return sanitized


class ExecutionRegistry:
    """Registro thread-safe, limitado e sem payloads sensíveis."""

    def __init__(
        self,
        *,
        max_executions: int = DEFAULT_MAX_EXECUTIONS,
        max_attempts_per_execution: int = DEFAULT_MAX_ATTEMPTS_PER_EXECUTION,
    ) -> None:
        self.max_executions = _validate_capacity("max_executions", max_executions)
        self.max_attempts_per_execution = _validate_capacity(
            "max_attempts_per_execution", max_attempts_per_execution
        )
        self._lock = threading.RLock()
        self._entries: OrderedDict[str, RegistryEntry] = OrderedDict()
        self._request_index: dict[str, str] = {}
        self._execution_request_keys: dict[str, str] = {}
        self._controls: dict[str, ExecutionControl] = {}

    def create(
        self,
        *,
        execution_id: str,
        request_id: RequestId,
        tool: str,
        control: ExecutionControl | None = None,
    ) -> RegistryEntry:
        """Criar uma execução e remover a mais antiga ao atingir o limite."""
        if not isinstance(execution_id, str) or not execution_id.strip():
            raise ValueError("execution_id must be a non-empty string")
        if not isinstance(tool, str) or not tool.strip():
            raise ValueError("tool must be a non-empty string")
        _validate_request_id(request_id)
        request_key = _request_key(request_id)

        with self._lock:
            if execution_id in self._entries:
                raise ValueError(f"execution_id already registered: {execution_id}")
            if request_key in self._request_index:
                raise ValueError("request_id is already registered")

            entry: RegistryEntry = {
                "execution_id": execution_id,
                "request_id": _public_request_id(request_id),
                "tool": tool,
                "state": "queued",
                "attempts": [],
            }
            self._entries[execution_id] = entry
            self._request_index[request_key] = execution_id
            self._execution_request_keys[execution_id] = request_key
            if control is not None:
                self._controls[execution_id] = control

            while len(self._entries) > self.max_executions:
                evicted_id, _ = self._entries.popitem(last=False)
                evicted_request_key = self._execution_request_keys.pop(evicted_id)
                self._request_index.pop(evicted_request_key, None)
                self._controls.pop(evicted_id, None)
            return copy.deepcopy(entry)

    def update_attempt(
        self, execution_id: str, snapshot: AttemptSnapshot
    ) -> RegistryEntry | None:
        """Acrescentar apenas campos permitidos e limitar o histórico."""
        if not isinstance(snapshot, Mapping):
            raise TypeError("snapshot must be a mapping")
        sanitized = _sanitize_attempt(snapshot)
        with self._lock:
            entry = self._entries.get(execution_id)
            if entry is None:
                return None
            attempts = entry["attempts"]
            attempts.append(sanitized)
            overflow = len(attempts) - self.max_attempts_per_execution
            if overflow > 0:
                del attempts[:overflow]
            attempt_id = sanitized.get("attempt_id")
            if isinstance(attempt_id, str):
                entry["current_attempt_id"] = attempt_id
            state = sanitized.get("state")
            if isinstance(state, str):
                entry["state"] = state
            return copy.deepcopy(entry)

    def finalize(
        self, execution_id: str, *, state: str | None = None
    ) -> RegistryEntry | None:
        """Finalizar uma execução, preservando um estado terminal já observado."""
        with self._lock:
            entry = self._entries.get(execution_id)
            if entry is None:
                return None
            if state is not None:
                entry["state"] = state
            elif entry["state"] not in _TERMINAL_STATES:
                entry["state"] = "completed"
            entry["finalized"] = True
            return copy.deepcopy(entry)

    def get(
        self,
        execution_id: str | None = None,
        *,
        request_id: RequestId | None = None,
    ) -> RegistryEntry | None:
        """Resolver request ids pelo valor bruto e retornar uma cópia sanitizada."""
        with self._lock:
            resolved_id = execution_id
            if resolved_id is None and request_id is not None:
                _validate_request_id(request_id)
                resolved_id = self._request_index.get(_request_key(request_id))
            entry = self._entries.get(resolved_id) if resolved_id is not None else None
            return copy.deepcopy(entry) if entry is not None else None

    def list(self, *, limit: int | None = None) -> list[RegistryEntry]:
        """Listar cópias na ordem de inserção, opcionalmente só as mais recentes."""
        if limit is not None:
            _validate_capacity("limit", limit)
        with self._lock:
            entries = list(self._entries.values())
            if limit is not None:
                entries = entries[-limit:]
            return copy.deepcopy(entries)

    def request_cancel(
        self,
        *,
        execution_id: str | None = None,
        request_id: RequestId | None = None,
        reason: str | None = None,
    ) -> RegistryEntry:
        """Solicitar cancelamento sem expor o objeto de controle."""
        with self._lock:
            resolved_id = execution_id
            if resolved_id is None and request_id is not None:
                _validate_request_id(request_id)
                resolved_id = self._request_index.get(_request_key(request_id))
            control = self._controls.get(resolved_id) if resolved_id is not None else None
        if resolved_id is None or control is None:
            return {"found": False, "requested": False, "execution_id": resolved_id}
        requested = control.request_cancel(reason)
        return {"found": True, "requested": requested, "execution_id": resolved_id}

    def abandon_all_nonterminal(self, *, reason: str = "client_abandoned") -> int:
        """Solicitar cancelamento dos controles associados a estados não terminais."""
        with self._lock:
            controls = [
                control
                for execution_id, control in self._controls.items()
                if self._entries[execution_id]["state"] not in _TERMINAL_STATES
            ]
        return sum(control.request_cancel(reason) for control in controls)
