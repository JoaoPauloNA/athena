"""Implementação do ciclo de vida de execução."""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

from .contracts import (
    TERMINAL_STATES,
    Clock,
    DeadlineKind,
    ExecutionDeadlines,
    ExecutionState,
)

_SAFE_CANCEL_REASONS = frozenset(
    {"cancelled", "client_abandoned", "shutdown", "user_requested"}
)

_ALLOWED_TRANSITIONS: dict[ExecutionState, frozenset[ExecutionState]] = {
    ExecutionState.QUEUED: frozenset(
        {
            ExecutionState.STARTING,
            ExecutionState.FAILED,
            ExecutionState.CANCELLED,
            ExecutionState.TIMED_OUT,
        }
    ),
    ExecutionState.STARTING: frozenset(
        {
            ExecutionState.RUNNING,
            ExecutionState.FAILED,
            ExecutionState.CANCELLED,
            ExecutionState.TIMED_OUT,
            ExecutionState.TERMINATION_UNCONFIRMED,
        }
    ),
    ExecutionState.RUNNING: TERMINAL_STATES,
}


class InvalidStateTransition(ValueError):
    """Erro gerado quando uma transição viola o ciclo de vida."""


class SystemClock:
    """Relógio monotônico padrão do processo."""

    def monotonic(self) -> float:
        """Retornar o instante monotônico atual."""
        return time.monotonic()


class CancellationToken:
    """Sinal de cancelamento em que o primeiro pedido prevalece."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancellation_requested = False
        self._cancel_reason: str | None = None

    @property
    def cancellation_requested(self) -> bool:
        """Indicar de forma sincronizada se há pedido de cancelamento."""
        with self._lock:
            return self._cancellation_requested

    @property
    def cancel_reason(self) -> str | None:
        """Retornar o motivo do primeiro pedido de cancelamento."""
        with self._lock:
            return self._cancel_reason

    def request_cancel(self, reason: str | None = None) -> bool:
        """Registrar atomicamente apenas o primeiro pedido."""
        with self._lock:
            if self._cancellation_requested:
                return False
            self._cancel_reason = reason
            self._cancellation_requested = True
            return True


def normalize_cancel_reason(reason: str | None) -> str:
    """Converter motivos livres em códigos públicos seguros."""
    normalized = (reason or "").strip().lower()
    if normalized in _SAFE_CANCEL_REASONS:
        return normalized
    return "cancelled"


class ExecutionRecord:
    """Registro sincronizado do ciclo de vida e de seus dois relógios."""

    def __init__(
        self,
        provider: str,
        *,
        profile: str | None = None,
        transport: str = "local",
        execution_id: str | None = None,
        attempt_id: str | None = None,
        deadlines: ExecutionDeadlines | None = None,
        clock: Clock | None = None,
        on_update: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.provider = provider
        self.profile = profile
        self.transport = transport
        self.execution_id = execution_id or str(uuid.uuid4())
        self.attempt_id = attempt_id or str(uuid.uuid4())
        self.on_update = on_update
        self._clock = clock or SystemClock()
        self._lock = threading.RLock()
        self._state = ExecutionState.QUEUED
        self._reason_code: str | None = None
        self.created_at_monotonic = self._clock.monotonic()
        self.last_activity_at_monotonic = self.created_at_monotonic
        self.absolute_deadline_s: float | None = None
        self.idle_deadline_s: float | None = None
        self.absolute_deadline_at: float | None = None
        self.idle_deadline_at: float | None = None
        configured = deadlines or ExecutionDeadlines()
        self.configure_deadlines(
            absolute_deadline_s=configured.absolute_timeout_s,
            idle_deadline_s=configured.idle_timeout_s,
        )

    @property
    def state(self) -> ExecutionState:
        """Retornar o estado atual de forma sincronizada."""
        with self._lock:
            return self._state

    def configure_deadlines(
        self,
        *,
        absolute_deadline_s: float | None,
        idle_deadline_s: float | None,
    ) -> None:
        """Configurar os relógios absoluto e idle sem combiná-los."""
        validated = ExecutionDeadlines(absolute_deadline_s, idle_deadline_s)
        with self._lock:
            now = self._clock.monotonic()
            self.absolute_deadline_s = validated.absolute_timeout_s
            self.idle_deadline_s = validated.idle_timeout_s
            self.absolute_deadline_at = (
                None
                if self.absolute_deadline_s is None
                else self.created_at_monotonic + self.absolute_deadline_s
            )
            self.last_activity_at_monotonic = now
            self.idle_deadline_at = (
                None
                if self.idle_deadline_s is None
                else now + self.idle_deadline_s
            )

    def transition(self, new_state: ExecutionState, *, reason: str | None = None) -> None:
        """Aplicar uma transição válida e rejeitar qualquer saída terminal."""
        callback: Callable[[dict[str, Any]], None] | None
        with self._lock:
            allowed = _ALLOWED_TRANSITIONS.get(self._state, frozenset())
            if new_state not in allowed:
                raise InvalidStateTransition(
                    f"transition from {self._state.value} to {new_state.value} is not allowed"
                )
            self._state = new_state
            self._reason_code = normalize_cancel_reason(reason) if reason else None
            payload = self._to_dict_unlocked()
            callback = self.on_update
        if callback is not None:
            callback(payload)

    def record_progress(self) -> bool:
        """Reiniciar somente o relógio idle em estados não terminais."""
        with self._lock:
            if self._state in TERMINAL_STATES:
                return False
            now = self._clock.monotonic()
            self.last_activity_at_monotonic = now
            if self.idle_deadline_s is not None:
                self.idle_deadline_at = now + self.idle_deadline_s
            return True

    def expired_deadline(self) -> DeadlineKind | None:
        """Identificar qual dos dois relógios independentes expirou."""
        with self._lock:
            now = self._clock.monotonic()
            if self.absolute_deadline_at is not None and now >= self.absolute_deadline_at:
                return DeadlineKind.ABSOLUTE
            if self.idle_deadline_at is not None and now >= self.idle_deadline_at:
                return DeadlineKind.IDLE
            return None

    def deadline_status(self) -> str | None:
        """Retornar o código textual do deadline expirado, quando houver."""
        expired = self.expired_deadline()
        return expired.value if expired is not None else None

    def to_dict(self) -> dict[str, Any]:
        """Serializar somente metadados permitidos e não sensíveis."""
        with self._lock:
            return self._to_dict_unlocked()

    def _to_dict_unlocked(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "execution_id": self.execution_id,
            "attempt_id": self.attempt_id,
            "provider": self.provider,
            "transport": self.transport,
            "state": self._state.value,
            "absolute_deadline_s": self.absolute_deadline_s,
            "idle_deadline_s": self.idle_deadline_s,
        }
        if self.profile is not None:
            payload["profile"] = self.profile
        if self._reason_code is not None:
            payload["reason"] = self._reason_code
        return payload
