"""Contratos da camada fina de exposicao das tools MCP."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, TypeAlias, runtime_checkable

from athena.execution import ExecutionControl
from athena.profiles import ServiceProfile
from athena.registry import ExecutionRegistryContract, RequestId
from athena.router import ComboRequest, ComboRouterContract
from athena.verifier import VerificationRequest, VerificationResult

ToolPayload: TypeAlias = Mapping[str, Any]
ControlFactory: TypeAlias = Callable[[], ExecutionControl]
Verifier: TypeAlias = Callable[[VerificationRequest, ExecutionControl], VerificationResult]


@runtime_checkable
class ProfileResolverContract(Protocol):
    """Resolver o perfil que sera entregue ao router."""

    def __call__(
        self,
        *,
        explicit_profile_id: object | None = None,
        provider_id: object | None = None,
        task_type: object | None = None,
        working_directory: object | None = None,
    ) -> ServiceProfile:
        """Retornar um perfil publico e conservador."""
        ...


@dataclass(frozen=True, slots=True)
class MCPServerDependencies:
    """Dependencias explicitas permitidas para a superficie MCP."""

    router: ComboRouterContract
    registry: ExecutionRegistryContract
    verifier: Verifier
    profile_resolver: ProfileResolverContract
    control_factory: ControlFactory
    shadow_emitter: object | None = None
    artifact_finalizer: object | None = None


@dataclass(frozen=True, slots=True)
class PreparedExecution:
    """Reserva interna criada antes de despachar trabalho demorado."""

    execution_id: str
    request_id: RequestId
    tool: str
    control: ExecutionControl


@runtime_checkable
class MCPServerContract(Protocol):
    """Tools invocaveis diretamente, sem transporte de rede."""

    def run_combo(
        self,
        combo: ComboRequest,
        *,
        request_id: RequestId,
        verification: VerificationRequest | None = None,
        prepared: PreparedExecution | None = None,
    ) -> ToolPayload:
        """Delegar um combo e opcionalmente sua verificacao."""
        ...

    def ask_provider(
        self,
        combo: ComboRequest,
        *,
        request_id: RequestId,
        provider_id: object | None = None,
        task_type: object | None = None,
        working_directory: object | None = None,
        verification: VerificationRequest | None = None,
        prepared: PreparedExecution | None = None,
    ) -> ToolPayload:
        """Delegar ao router uma requisicao preparada para um provider."""
        ...

    def get_execution(
        self,
        execution_id: str | None = None,
        *,
        request_id: RequestId | None = None,
    ) -> ToolPayload:
        """Consultar uma execucao sanitizada."""
        ...

    def list_executions(self, *, limit: int | None = None) -> ToolPayload:
        """Listar execucoes sanitizadas."""
        ...

    def cancel_execution(
        self,
        execution_id: str | None = None,
        *,
        request_id: RequestId | None = None,
        reason: str | None = None,
    ) -> ToolPayload:
        """Solicitar cancelamento idempotente por qualquer identificador."""
        ...

    def prepare_execution(
        self,
        tool: str,
        *,
        request_id: RequestId,
        execution_id: str | None = None,
    ) -> PreparedExecution:
        """Reservar id e controle antes do trabalho demorado."""
        ...

    def abandon_nonterminal(self, *, reason: str = "client_abandoned") -> int:
        """Sinalizar abandono das execuções ainda ativas."""
        ...
