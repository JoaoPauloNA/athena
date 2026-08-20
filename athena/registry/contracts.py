"""Contratos públicos do registro limitado de execuções."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, TypeAlias, runtime_checkable

from athena.execution import ExecutionControl

RequestId: TypeAlias = str | int
RegistryEntry: TypeAlias = dict[str, Any]
AttemptSnapshot: TypeAlias = Mapping[str, Any]


@runtime_checkable
class ExecutionRegistryContract(Protocol):
    """Superfície de leitura e atualização consumível pelo servidor MCP."""

    def create(
        self,
        *,
        execution_id: str,
        request_id: RequestId,
        tool: str,
        control: ExecutionControl | None = None,
    ) -> RegistryEntry:
        """Criar uma execução sanitizada e aplicar o limite global."""
        ...

    def update_attempt(
        self, execution_id: str, snapshot: AttemptSnapshot
    ) -> RegistryEntry | None:
        """Adicionar uma tentativa sanitizada à execução, se ela existir."""
        ...

    def finalize(
        self, execution_id: str, *, state: str | None = None
    ) -> RegistryEntry | None:
        """Marcar uma execução existente como finalizada."""
        ...

    def get(
        self,
        execution_id: str | None = None,
        *,
        request_id: RequestId | None = None,
    ) -> RegistryEntry | None:
        """Buscar por identificador da execução ou request id bruto."""
        ...

    def list(self, *, limit: int | None = None) -> list[RegistryEntry]:
        """Listar snapshots do mais antigo ao mais recente."""
        ...

    def request_cancel(
        self,
        *,
        execution_id: str | None = None,
        request_id: RequestId | None = None,
        reason: str | None = None,
    ) -> RegistryEntry:
        """Encaminhar cancelamento ao controle privado de uma execução."""
        ...

    def abandon_all_nonterminal(self, *, reason: str = "client_abandoned") -> int:
        """Solicitar cancelamento de todas as execuções ainda ativas."""
        ...
