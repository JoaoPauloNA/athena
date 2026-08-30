"""Contratos públicos para orquestração segura de combos."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from aegis.contracts import FailureCondition, ServiceProfile

from athena.bridge import RunRequest, RunResult
from athena.execution import ExecutionControl, ExecutionDeadlines, ExecutionRecord


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


class RoutingAbstained(AllAttemptsFailed):
    """A autoridade interna recusou produzir um plano executável."""


@dataclass(frozen=True, slots=True)
class RoutingContext:
    """Contexto fechado consumido pela rota determinística Zeus/Nike."""

    task_type: str
    primary_domain: str
    risk_level: str
    required_capabilities: tuple[str, ...]
    explicit_agent_tag: str | None = None

    def __post_init__(self) -> None:
        token = re.compile(r"[a-z][a-z0-9_.:-]{0,127}")
        if isinstance(self.required_capabilities, (str, bytes)) or not isinstance(
            self.required_capabilities, Sequence
        ):
            raise TypeError("ROUTE_CONTEXT_INVALID")
        capabilities = tuple(self.required_capabilities)
        if (
            not isinstance(self.task_type, str)
            or token.fullmatch(self.task_type) is None
            or not isinstance(self.primary_domain, str)
            or token.fullmatch(self.primary_domain) is None
            or self.risk_level not in ("low", "medium", "high", "critical")
            or not 1 <= len(capabilities) <= 64
            or any(
                not isinstance(capability, str)
                or token.fullmatch(capability) is None
                for capability in capabilities
            )
            or len(set(capabilities)) != len(capabilities)
            or (
                self.explicit_agent_tag is not None
                and (
                    not isinstance(self.explicit_agent_tag, str)
                    or token.fullmatch(self.explicit_agent_tag) is None
                )
            )
        ):
            raise ValueError("ROUTE_CONTEXT_INVALID")
        object.__setattr__(self, "required_capabilities", tuple(sorted(capabilities)))


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
    tests: tuple[str, ...] = ()

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
        tests = tuple(self.tests)
        if any(not isinstance(test, str) or not test for test in tests):
            raise ValueError("tests must contain non-empty strings")
        object.__setattr__(self, "tests", tests)


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


@runtime_checkable
class RoutingAuthorityContract(Protocol):
    """Selecionar uma receita antes do router de execução/fallback."""

    def plan(
        self,
        combo: ComboRequest,
        context: RoutingContext | None,
        *,
        direct_provider_id: str | None = None,
    ) -> ComboRequest:
        """Retornar um plano interno ou abster sem executar processos."""
        ...


@runtime_checkable
class AttemptAuthorizerContract(Protocol):
    """Preparar uma tentativa autorizada sem executar ou escolher fallback."""

    def prepare_attempt(
        self,
        request: RunRequest,
        execution: ExecutionRecord,
        *,
        fallback_declared: bool,
        tests: tuple[str, ...],
    ) -> RunRequest:
        """Retornar o mesmo plano limitado por autorização interna."""
        ...
