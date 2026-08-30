"""Contratos públicos da ponte de execução local."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from athena.execution import (
    DeadlineKind,
    ExecutionControl,
    ExecutionRecord,
    ExecutionState,
)
from athena.lease import DirectoryLeaseContract


@dataclass(frozen=True, slots=True)
class RunRequest:
    """Descrição segura e independente de provedor para uma execução local."""

    command: Sequence[str]
    cwd: str | Path
    env: Mapping[str, str] = field(default_factory=dict)
    use_pty: bool = False
    lease_timeout_s: float | None = None
    termination_grace_s: float = 0.5
    inherit_environment: bool = True
    authorization: object | None = None


@dataclass(frozen=True, slots=True)
class RunResult:
    """Resultado capturado depois de a execução alcançar um estado terminal."""

    command: tuple[str, ...]
    cwd: Path
    state: ExecutionState
    exit_code: int | None
    stdout: str
    stderr: str
    duration_s: float
    expired_deadline: DeadlineKind | None = None
    error: str | None = None

    @property
    def output(self) -> str:
        """Retornar a saída observável do processo."""
        return self.stdout + self.stderr

    @property
    def timed_out(self) -> bool:
        """Indicar se a execução terminou por deadline do lifecycle."""
        return self.state is ExecutionState.TIMED_OUT


@runtime_checkable
class BridgeRunnerContract(Protocol):
    """Executar um processo dirigindo apenas contratos públicos do núcleo."""

    def run(
        self,
        request: RunRequest,
        execution: ExecutionRecord,
        lease: DirectoryLeaseContract,
        *,
        control: ExecutionControl | None = None,
    ) -> RunResult:
        """Executar do estado QUEUED até exatamente um estado terminal."""
        ...
