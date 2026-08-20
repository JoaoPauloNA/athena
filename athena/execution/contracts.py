"""Contratos públicos das primitivas de execução."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable


class ExecutionState(str, Enum):
    """Estado observável de uma execução."""

    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    TERMINATION_UNCONFIRMED = "termination_unconfirmed"


class DeadlineKind(str, Enum):
    """Relógio responsável por um deadline expirado."""

    ABSOLUTE = "absolute_deadline"
    IDLE = "idle_deadline"


TERMINAL_STATES = frozenset(
    {
        ExecutionState.COMPLETED,
        ExecutionState.FAILED,
        ExecutionState.CANCELLED,
        ExecutionState.TIMED_OUT,
        ExecutionState.TERMINATION_UNCONFIRMED,
    }
)


@dataclass(frozen=True, slots=True)
class ExecutionDeadlines:
    """Configuração independente dos deadlines absoluto e de inatividade."""

    absolute_timeout_s: float | None = None
    idle_timeout_s: float | None = None

    def __post_init__(self) -> None:
        """Rejeitar durações que não representam intervalos positivos."""
        for name, value in (
            ("absolute_timeout_s", self.absolute_timeout_s),
            ("idle_timeout_s", self.idle_timeout_s),
        ):
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be greater than zero")


@runtime_checkable
class Clock(Protocol):
    """Fonte monotônica de tempo injetável."""

    def monotonic(self) -> float:
        """Retornar o instante monotônico atual."""
        ...


@runtime_checkable
class ExecutionControl(Protocol):
    """Contrato thread-safe para solicitação de cancelamento."""

    @property
    def cancellation_requested(self) -> bool:
        """Indicar se o cancelamento já foi solicitado."""
        ...

    @property
    def cancel_reason(self) -> str | None:
        """Retornar o motivo associado ao primeiro pedido."""
        ...

    def request_cancel(self, reason: str | None = None) -> bool:
        """Registrar o primeiro pedido e informar se ele venceu a corrida."""
        ...
