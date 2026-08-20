"""Contratos públicos para orquestração segura de combos."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from aegis.contracts import FailureCondition, ServiceProfile

from athena.bridge import RunRequest, RunResult
from athena.execution import ExecutionControl, ExecutionDeadlines


class ComboError(RuntimeError):
    """Erro terminal produzido pela orquestração de um combo."""


class AllAttemptsFailed(ComboError):
    """Todas as tentativas autorizadas terminaram sem sucesso."""

    def __init__(self, message: str, *, last_result: RunResult | None = None) -> None:
        self.last_result = last_result
        super().__init__(message)


class FallbackBlocked(AllAttemptsFailed):
    """O fallback foi bloqueado porque o término anterior não foi confirmado."""


class ComboDeadlineExceeded(AllAttemptsFailed):
    """O deadline global expirou antes de uma nova etapa segura."""


@dataclass(frozen=True, slots=True)
class ComboAttempt:
    """Uma tentativa ordenada do combo executada exclusivamente pelo bridge."""

    provider: str
    request: RunRequest
    deadlines: ExecutionDeadlines = field(default_factory=ExecutionDeadlines)
    failure_condition: FailureCondition = FailureCondition.PROVIDER_ERROR

    def __post_init__(self) -> None:
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise ValueError("provider must be a non-empty string")


@dataclass(frozen=True, slots=True)
class ComboRequest:
    """Sequência e política compartilhada de uma execução de combo."""

    attempts: Sequence[ComboAttempt]
    profile: ServiceProfile | str | None
    overall_timeout_s: float | None = None
    execution_id: str | None = None

    def __post_init__(self) -> None:
        attempts = tuple(self.attempts)
        if not attempts:
            raise ValueError("attempts must not be empty")
        if not all(isinstance(attempt, ComboAttempt) for attempt in attempts):
            raise TypeError("attempts must contain only ComboAttempt values")
        object.__setattr__(self, "attempts", attempts)

        timeout = self.overall_timeout_s
        if timeout is not None:
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
                raise TypeError("overall_timeout_s must be a positive finite number or None")
            if timeout <= 0 or not math.isfinite(timeout):
                raise ValueError(
                    "overall_timeout_s must be a positive finite number or None"
                )
            object.__setattr__(self, "overall_timeout_s", float(timeout))

        if self.execution_id is not None and (
            not isinstance(self.execution_id, str) or not self.execution_id.strip()
        ):
            raise ValueError("execution_id must be a non-empty string or None")


@runtime_checkable
class ComboRouterContract(Protocol):
    """Executar combos sem sobrepor tentativas no mesmo workspace."""

    def run(
        self,
        combo: ComboRequest,
        *,
        control: ExecutionControl | None = None,
    ) -> RunResult:
        """Retornar o primeiro sucesso ou levantar um erro terminal do combo."""
        ...
