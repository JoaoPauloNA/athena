"""Contratos fechados MULTI-0 — Harmonia, plano de equipe e reason codes."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable

SCHEMA_VERSION = "harmonia.team.v1"
CONTRACT_VERSION = "multi-0.1"

MAX_SUBTASKS = 64
MAX_DEPENDENCIES = 32
MAX_SCOPE_PATHS = 64
MAX_ID_LEN = 128
MAX_REASON_CODES = 16
MAX_QUEUE_DEPTH = 64
MAX_WORKERS = 8
MAX_CPU_TOKENS = 8
MAX_RAM_MB = 65_536
MAX_GPU_TOKENS = 4
MAX_PROVIDER_TOKENS = 4
MAX_GRANULAR_WRITE_PATHS = 16
RESERVATION_TIMEOUT_S = 0.05

_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")

GLOBAL_OPERATION_TYPES = frozenset(
    {
        "git",
        "migration",
        "formatter",
        "codegen",
        "lockfile",
        "repo",
    }
)

VALID_OPERATION_TYPES = frozenset(
    {
        "file_edit",
        "read_only",
        *GLOBAL_OPERATION_TYPES,
    }
)

REASON_CYCLE = "HARMONIA_CYCLE_DETECTED"
REASON_DUPLICATE_ID = "HARMONIA_DUPLICATE_ID"
REASON_MISSING_DEPENDENCY = "HARMONIA_MISSING_DEPENDENCY"
REASON_LIMIT_EXCEEDED = "HARMONIA_LIMIT_EXCEEDED"
REASON_BUSY = "HARMONIA_BUSY"
REASON_SCOPE_CONFLICT = "HARMONIA_SCOPE_CONFLICT"
REASON_PATH_INVALID = "HARMONIA_PATH_INVALID"
REASON_WORKTREE_DENIED = "HARMONIA_WORKTREE_DENIED"
REASON_WORKTREE_CLEANUP_FAILED = "HARMONIA_WORKTREE_CLEANUP_FAILED"
REASON_OUT_OF_SCOPE = "HARMONIA_OUT_OF_SCOPE"
REASON_SUBTASK_FAILED = "HARMONIA_SUBTASK_FAILED"
REASON_SUBTASK_CANCELLED = "HARMONIA_SUBTASK_CANCELLED"
REASON_SUBTASK_TIMEOUT = "HARMONIA_SUBTASK_TIMEOUT"
REASON_UNAUTHORIZED = "HARMONIA_UNAUTHORIZED"
REASON_INVALID_PLAN = "HARMONIA_INVALID_PLAN"
REASON_EXECUTOR_NON_CANCELLABLE = "HARMONIA_EXECUTOR_NON_CANCELLABLE"
REASON_EXECUTOR_NON_TERMINATED = "HARMONIA_EXECUTOR_NON_TERMINATED"
REASON_INVENTORY_EXCEEDED = "HARMONIA_INVENTORY_EXCEEDED"
REASON_EVIDENCE_CAPTURE_FAILED = "HARMONIA_EVIDENCE_CAPTURE_FAILED"

TERMINATION_WAIT_S = 2.0
CLEANUP_TIMEOUT_S = 2.0
EVIDENCE_CAPTURE_TIMEOUT_S = 1.0
SHUTDOWN_TIMEOUT_S = 5.0
MAX_EVIDENCE_PATHS = 32
MAX_EVIDENCE_BYTES = 65_536


class IsolationStrategy(str, Enum):
    GRANULAR_LEASE = "granular_lease"
    WORKTREE = "worktree"


class SubtaskState(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _validate_budget_field(value: object, *, name: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if not 0 <= value <= maximum:
        raise ValueError(f"{name} out of bounds")
    return value


@dataclass(frozen=True, slots=True)
class ResourceBudget:
    cpu_tokens: int
    ram_mb: int
    gpu_tokens: int
    provider_tokens: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "cpu_tokens",
            _validate_budget_field(self.cpu_tokens, name="cpu_tokens", maximum=MAX_CPU_TOKENS),
        )
        object.__setattr__(
            self,
            "ram_mb",
            _validate_budget_field(self.ram_mb, name="ram_mb", maximum=MAX_RAM_MB),
        )
        object.__setattr__(
            self,
            "gpu_tokens",
            _validate_budget_field(self.gpu_tokens, name="gpu_tokens", maximum=MAX_GPU_TOKENS),
        )
        object.__setattr__(
            self,
            "provider_tokens",
            _validate_budget_field(
                self.provider_tokens, name="provider_tokens", maximum=MAX_PROVIDER_TOKENS
            ),
        )


@dataclass(frozen=True, slots=True)
class SubtaskSpec:
    subtask_id: str
    dependencies: tuple[str, ...]
    worker_id: str
    read_scope: tuple[str, ...]
    write_scope: tuple[str, ...]
    operation_type: str
    resources: ResourceBudget
    seal_hash: str
    deadline_s: float | None = None


@dataclass(frozen=True, slots=True)
class TeamPlan:
    task_id: str
    subtasks: tuple[SubtaskSpec, ...]
    max_parallelism: int
    project_parallelism: int
    aegis_parallelism: int


@dataclass(frozen=True, slots=True)
class ScheduleGroup:
    group_id: str
    subtask_ids: tuple[str, ...]
    parallelism_reason: str


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    schema_version: str
    contract_version: str
    task_id: str
    groups: tuple[ScheduleGroup, ...]
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BusyResult:
    reason_codes: tuple[str, ...]
    waiters_ahead: int
    estimated_wait_s: float


@dataclass(frozen=True, slots=True)
class SubtaskOutcome:
    subtask_id: str
    state: SubtaskState
    reason_codes: tuple[str, ...]
    isolation: IsolationStrategy | None = None
    altered_paths: tuple[str, ...] = ()
    evidence_digest: str | None = None
    evidence_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TeamRunResult:
    task_id: str
    outcomes: tuple[SubtaskOutcome, ...]
    reason_codes: tuple[str, ...]


def _closed_string_sequence(value: object, *, name: str, maximum: int) -> tuple[str, ...]:
    if (
        isinstance(value, (str, bytes, bytearray, Mapping))
        or not isinstance(value, Sequence)
    ):
        raise TypeError(f"{name} invalid")
    normalized = tuple(value)
    if len(normalized) > maximum:
        raise ValueError(REASON_LIMIT_EXCEEDED)
    return normalized


def _validate_identifier(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(f"{name} invalid")
    return value


def _validate_digest(value: str) -> str:
    if not isinstance(value, str) or not _DIGEST_PATTERN.fullmatch(value):
        raise ValueError(REASON_INVALID_PLAN)
    return value


def _validate_parallelism(value: int, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} invalid")
    if not 1 <= value <= MAX_WORKERS:
        raise ValueError(REASON_LIMIT_EXCEEDED)
    return value


def _validate_budget(raw: object) -> ResourceBudget:
    if not isinstance(raw, Mapping):
        raise TypeError(REASON_INVALID_PLAN)
    for field in ("cpu_tokens", "ram_mb", "gpu_tokens", "provider_tokens"):
        if field not in raw:
            raise ValueError(REASON_INVALID_PLAN)
        if isinstance(raw[field], bool) or not isinstance(raw[field], int):
            raise TypeError(REASON_INVALID_PLAN)
    try:
        return ResourceBudget(
            raw["cpu_tokens"],
            raw["ram_mb"],
            raw["gpu_tokens"],
            raw["provider_tokens"],
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(REASON_INVALID_PLAN) from exc


def validate_resource_budget(budget: ResourceBudget) -> ResourceBudget:
    """Validar orçamento construído diretamente — rejeita coerção bool/string."""
    if not isinstance(budget, ResourceBudget):
        raise TypeError(REASON_INVALID_PLAN)
    return budget


def validate_subtask_spec(subtask: SubtaskSpec) -> SubtaskSpec:
    """Validar subtarefa construída diretamente antes de admissão."""
    if not isinstance(subtask, SubtaskSpec):
        raise TypeError(REASON_INVALID_PLAN)
    _validate_identifier(subtask.subtask_id, name="subtask_id")
    _validate_identifier(subtask.worker_id, name="worker_id")
    _validate_digest(subtask.seal_hash)
    validate_resource_budget(subtask.resources)
    if subtask.operation_type not in VALID_OPERATION_TYPES:
        raise ValueError(REASON_INVALID_PLAN)
    if len(subtask.dependencies) > MAX_DEPENDENCIES:
        raise ValueError(REASON_LIMIT_EXCEEDED)
    if len(subtask.read_scope) > MAX_SCOPE_PATHS or len(subtask.write_scope) > MAX_SCOPE_PATHS:
        raise ValueError(REASON_LIMIT_EXCEEDED)
    if subtask.deadline_s is not None:
        if isinstance(subtask.deadline_s, bool) or not isinstance(
            subtask.deadline_s, (int, float)
        ):
            raise ValueError(REASON_INVALID_PLAN)
        if subtask.deadline_s <= 0 or not math.isfinite(float(subtask.deadline_s)):
            raise ValueError(REASON_INVALID_PLAN)
    return subtask


def validate_team_plan(plan: TeamPlan) -> TeamPlan:
    """Validar plano construído diretamente, incluindo DAG."""
    if not isinstance(plan, TeamPlan):
        raise TypeError(REASON_INVALID_PLAN)
    _validate_identifier(plan.task_id, name="task_id")
    _validate_parallelism(plan.max_parallelism, name="max_parallelism")
    _validate_parallelism(plan.project_parallelism, name="project_parallelism")
    _validate_parallelism(plan.aegis_parallelism, name="aegis_parallelism")
    if not plan.subtasks or len(plan.subtasks) > MAX_SUBTASKS:
        raise ValueError(REASON_INVALID_PLAN)
    for subtask in plan.subtasks:
        validate_subtask_spec(subtask)
    from .dag import validate_dag

    validate_dag(plan)
    return plan


def parse_subtask(raw: Mapping[str, Any]) -> SubtaskSpec:
    allowed = {
        "subtask_id",
        "dependencies",
        "worker_id",
        "read_scope",
        "write_scope",
        "operation_type",
        "resources",
        "seal_hash",
        "deadline_s",
    }
    if set(raw) - allowed:
        raise ValueError(REASON_INVALID_PLAN)
    deadline = raw.get("deadline_s")
    if deadline is not None:
        if isinstance(deadline, bool) or not isinstance(deadline, (int, float)):
            raise ValueError(REASON_INVALID_PLAN)
        deadline_value = float(deadline)
        if deadline_value <= 0 or not math.isfinite(deadline_value):
            raise ValueError(REASON_INVALID_PLAN)
    else:
        deadline_value = None
    operation = raw["operation_type"]
    if operation not in VALID_OPERATION_TYPES:
        raise ValueError(REASON_INVALID_PLAN)
    return SubtaskSpec(
        subtask_id=_validate_identifier(raw["subtask_id"], name="subtask_id"),
        dependencies=_closed_string_sequence(
            raw.get("dependencies", ()),
            name="dependencies",
            maximum=MAX_DEPENDENCIES,
        ),
        worker_id=_validate_identifier(raw["worker_id"], name="worker_id"),
        read_scope=_closed_string_sequence(
            raw.get("read_scope", ()),
            name="read_scope",
            maximum=MAX_SCOPE_PATHS,
        ),
        write_scope=_closed_string_sequence(
            raw.get("write_scope", ()),
            name="write_scope",
            maximum=MAX_SCOPE_PATHS,
        ),
        operation_type=operation,
        resources=_validate_budget(raw["resources"]),
        seal_hash=_validate_digest(raw["seal_hash"]),
        deadline_s=deadline_value,
    )


def parse_team_plan(raw: Mapping[str, Any]) -> TeamPlan:
    allowed = {
        "task_id",
        "subtasks",
        "max_parallelism",
        "project_parallelism",
        "aegis_parallelism",
    }
    if set(raw) - allowed:
        raise ValueError(REASON_INVALID_PLAN)
    subtasks_raw = _closed_string_sequence(
        raw["subtasks"], name="subtasks", maximum=MAX_SUBTASKS
    )
    if not subtasks_raw:
        raise ValueError(REASON_INVALID_PLAN)
    subtasks = tuple(parse_subtask(item) for item in subtasks_raw)  # type: ignore[arg-type]
    return TeamPlan(
        task_id=_validate_identifier(raw["task_id"], name="task_id"),
        subtasks=subtasks,
        max_parallelism=_validate_parallelism(raw["max_parallelism"], name="max_parallelism"),
        project_parallelism=_validate_parallelism(
            raw["project_parallelism"], name="project_parallelism"
        ),
        aegis_parallelism=_validate_parallelism(
            raw["aegis_parallelism"], name="aegis_parallelism"
        ),
    )


@runtime_checkable
class SealAuthorizer(Protocol):
    """Contrato injetado — cada seal_hash deve ser autorizado antes da execução."""

    def authorize_subtask(
        self,
        *,
        task_id: str,
        subtask: SubtaskSpec,
        read_paths: tuple[str, ...],
        write_paths: tuple[str, ...],
        isolation: IsolationStrategy,
    ) -> bool:
        """Retornar True somente se o selo cobre tarefa/subtarefa/escopo/recursos."""
        ...


@runtime_checkable
class WorktreeAuthority(Protocol):
    """Autoridade injetada para worktrees — produção falha fechado sem implementação."""

    def create_worktree(
        self,
        *,
        repository_root: str,
        base_ref: str,
        opaque_name: str,
    ) -> str:
        """Criar worktree contida e retornar caminho absoluto."""
        ...

    def remove_worktree(self, worktree_path: str) -> None:
        """Remover apenas worktree criada por esta autoridade."""
        ...


@runtime_checkable
class SubtaskExecutor(Protocol):
    """Executor injetado — Harmonia nunca executa shell diretamente."""

    def execute(
        self,
        *,
        subtask: SubtaskSpec,
        workspace_root: str,
        attempt_id: str,
    ) -> tuple[int, tuple[str, ...]]:
        """Executar subtarefa e retornar (exit_code, altered_paths)."""
        ...


class CancellableSubtaskExecutor(SubtaskExecutor, Protocol):
    """Executor que suporta cancelamento com confirmação de término."""

    def cancel(self, *, attempt_id: str) -> None:
        """Sinalizar cancelamento da tentativa."""
        ...

    def wait_terminated(self, *, attempt_id: str, deadline_s: float) -> bool:
        """Aguardar término positivo até deadline; False se ainda ativo."""
        ...


@runtime_checkable
class ScopeEnforcementAuthority(Protocol):
    """Autoridade que prova atribuição exata de escopo em execução paralela."""

    def proves_exact_parallel_scope(
        self,
        *,
        task_id: str,
        subtask: SubtaskSpec,
        read_paths: tuple[str, ...],
        write_paths: tuple[str, ...],
    ) -> bool:
        """True somente se escopo paralelo é comprovadamente isolável sem worktree."""
        ...


class HarmoniaError(ValueError):
    """Erro de validação ou política com reason codes sanitizados."""

    def __init__(self, *reason_codes: str) -> None:
        codes = tuple(code for code in reason_codes if code)[:MAX_REASON_CODES]
        super().__init__(codes[0] if codes else REASON_INVALID_PLAN)
        self.reason_codes = codes


class WorktreeDeniedError(HarmoniaError):
    """Worktree solicitada sem autoridade injetada."""

    def __init__(self) -> None:
        super().__init__(REASON_WORKTREE_DENIED)
