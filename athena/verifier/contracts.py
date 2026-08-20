"""Contratos públicos para verificação em fases."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from athena.execution import ExecutionDeadlines, ExecutionState


class VerificationPhase(str, Enum):
    """Fases independentes do pipeline de verificação."""

    DETERMINISTIC = "deterministic"
    ADVISORY = "advisory"


class FindingStatus(str, Enum):
    """Resultado observável de uma checagem individual."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class FileClaim:
    """Alegação objetiva de que um arquivo existe."""

    path: str | Path

    def __post_init__(self) -> None:
        if not str(self.path).strip():
            raise ValueError("file claim path must not be empty")


@dataclass(frozen=True, slots=True)
class CommandClaim:
    """Alegação objetiva de que um comando termina com código zero."""

    command: Sequence[str]

    def __post_init__(self) -> None:
        command = tuple(self.command)
        if not command or not all(isinstance(part, str) and part for part in command):
            raise ValueError("command claim must contain non-empty string arguments")
        object.__setattr__(self, "command", command)


@dataclass(frozen=True, slots=True)
class VerificationFinding:
    """Evidência produzida por uma checagem."""

    phase: VerificationPhase
    subject: str
    status: FindingStatus
    detail: str = ""


AdvisoryCheck = Callable[[], VerificationFinding | bool | None]


@dataclass(frozen=True, slots=True)
class VerificationRequest:
    """Entradas e limites independentes de cada fase."""

    files: Sequence[FileClaim] = ()
    commands: Sequence[CommandClaim] = ()
    advisory_checks: Sequence[AdvisoryCheck] = ()
    working_directory: str | Path | None = None
    repository_root: str | Path | None = None
    deterministic_deadlines: ExecutionDeadlines = field(
        default_factory=ExecutionDeadlines
    )
    advisory_deadlines: ExecutionDeadlines = field(default_factory=ExecutionDeadlines)
    execution_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "files", tuple(self.files))
        object.__setattr__(self, "commands", tuple(self.commands))
        object.__setattr__(self, "advisory_checks", tuple(self.advisory_checks))
        if not all(isinstance(claim, FileClaim) for claim in self.files):
            raise TypeError("files must contain only FileClaim values")
        if not all(isinstance(claim, CommandClaim) for claim in self.commands):
            raise TypeError("commands must contain only CommandClaim values")
        if not all(callable(check) for check in self.advisory_checks):
            raise TypeError("advisory_checks must contain only callables")


@dataclass(frozen=True, slots=True)
class VerificationPhaseResult:
    """Resultado e lifecycle confirmado de uma fase."""

    phase: VerificationPhase
    findings: tuple[VerificationFinding, ...]
    execution: Mapping[str, object]
    termination_confirmed: bool

    @property
    def state(self) -> ExecutionState:
        """Retornar o estado terminal registrado pela camada de execução."""
        return ExecutionState(str(self.execution["state"]))

    @property
    def passed(self) -> bool:
        """Indicar ausência de falhas e término confirmado."""
        return (
            self.state is ExecutionState.COMPLETED
            and self.termination_confirmed
            and all(
                finding.status is not FindingStatus.FAILED
                for finding in self.findings
            )
        )


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Veredito composto; a fase advisory nunca altera a aceitação."""

    deterministic: VerificationPhaseResult
    advisory: VerificationPhaseResult

    @property
    def accepted(self) -> bool:
        """Bloquear somente quando a fase determinística não passa."""
        return self.deterministic.passed
