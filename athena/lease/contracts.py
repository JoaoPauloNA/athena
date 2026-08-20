"""Contrato público para leases locais de diretório."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


class LeaseAcquisitionTimeout(TimeoutError):
    """A aquisição não ocorreu dentro do limite informado."""


class LeaseOwnershipError(RuntimeError):
    """A operação foi solicitada por quem não possui o lease."""


@runtime_checkable
class DirectoryLeaseContract(Protocol):
    """Serializar acesso a diretórios exclusivamente dentro deste processo.

    Esta é deliberadamente uma coordenação em memória: ela nunca coordena
    processos diferentes nem hosts diferentes. A chave de cada lease é o
    diretório canônico, com caminhos absolutos e links simbólicos resolvidos.
    """

    def canonicalize(self, directory: str | Path) -> Path:
        """Retornar a chave canônica absoluta do diretório."""
        ...

    def acquire(
        self,
        directory: str | Path,
        execution_id: str,
        attempt_id: str,
        *,
        timeout: float | None = None,
    ) -> Path:
        """Adquirir o diretório para uma tentativa ou expirar pelo timeout."""
        ...

    def transfer(
        self,
        directory: str | Path,
        execution_id: str,
        current_attempt_id: str,
        next_attempt_id: str,
    ) -> Path:
        """Transferir atomicamente a posse entre tentativas da mesma execução."""
        ...

    def release(
        self,
        directory: str | Path,
        execution_id: str,
        attempt_id: str,
    ) -> None:
        """Liberar o diretório possuído pela tentativa informada."""
        ...
