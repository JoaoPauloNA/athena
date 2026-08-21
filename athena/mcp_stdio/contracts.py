"""Contratos do transporte MCP sobre JSON-RPC delimitado por linhas."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, TextIO, runtime_checkable

JsonObject = dict[str, Any]


@dataclass(frozen=True, slots=True)
class PreparedToolCall:
    """Chamada validada e reservada antes de entrar no executor."""

    name: str
    arguments: Mapping[str, Any]
    request_id: str | int
    reservation: object


@dataclass(frozen=True, slots=True)
class StdioTransport:
    """Streams explícitos usados pelo servidor, sem depender dos globais."""

    stdin: TextIO
    stdout: TextIO
    stderr: TextIO


@runtime_checkable
class MCPApplicationContract(Protocol):
    """Aplicação síncrona consumida pelo transporte JSON-RPC."""

    @property
    def tools(self) -> Sequence[Mapping[str, Any]]:
        """Descrever apenas as tools públicas desta aplicação."""
        ...

    def is_long_running(self, name: object) -> bool:
        """Indicar se a chamada deve ser preparada e executada em worker."""
        ...

    def prepare_long_call(
        self,
        name: str,
        arguments: Mapping[str, Any],
        request_id: object,
    ) -> PreparedToolCall:
        """Validar identificadores e reservar a execução atomicamente."""
        ...

    def call(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        request_id: object,
        prepared: PreparedToolCall | None = None,
    ) -> Mapping[str, Any]:
        """Despachar uma tool e retornar seu payload MCP."""
        ...

    def abandon_nonterminal(self) -> int:
        """Sinalizar abandono das execuções ainda ativas."""
        ...
