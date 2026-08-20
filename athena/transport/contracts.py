"""Contratos públicos para construção e execução de transportes remotos."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from athena.execution import ExecutionState


@dataclass(frozen=True, slots=True)
class SSHKeyAuthentication:
    """Autenticação SSH não interativa, exclusivamente por chave privada."""

    identity_file: str
    password: str | None = None

    def __post_init__(self) -> None:
        """Exigir uma chave e rejeitar qualquer configuração de senha."""
        if self.password is not None:
            raise ValueError("SSH password authentication is not supported")
        if not isinstance(self.identity_file, str) or not self.identity_file.strip():
            raise ValueError("identity_file must be a non-empty string")


@dataclass(frozen=True, slots=True)
class RemoteProcessOutcome:
    """Saída bruta produzida por um runner injetado."""

    return_code: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


@dataclass(frozen=True, slots=True)
class RemoteExecutionResult:
    """Resultado remoto conservador, sem afirmar término no destino."""

    argv: tuple[str, ...]
    state: ExecutionState
    return_code: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


@runtime_checkable
class RemoteRunner(Protocol):
    """Executor substituível; testes podem fornecê-lo sem abrir rede."""

    def run(
        self, argv: tuple[str, ...], *, timeout_s: float | None = None
    ) -> RemoteProcessOutcome:
        """Executar um argv já construído e devolver sua observação local."""
        ...
