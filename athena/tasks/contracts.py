"""Contratos públicos do armazenamento durável de submissão de tarefa."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

MAX_IDEMPOTENCY_KEY_BYTES = 256
MAX_TASK_TYPE_LENGTH = 128
MAX_INPUT_BYTES = 32 * 1024
MAX_PROJECT_REF_BYTES = 1024
MAX_CANONICAL_TASK_BYTES = 64 * 1024
MAX_JSON_DEPTH = 32
MAX_JSON_ITEMS = 10_000
MIN_PRIORITY = 0
MAX_PRIORITY = 9
DEFAULT_PRIORITY = 5

# Stable validation statuses — the only values permitted in TerminalProjection
VALID_VALIDATION_STATUSES = frozenset({"pass", "fail", "inconclusive", "escalate"})
# Stable chronos actions — the only values permitted in TerminalProjection
VALID_CHRONOS_ACTIONS = frozenset({"CLOSED", "HUMAN_REVIEW"})
# Stable delivery status — invariant per EG-3A
DELIVERY_STATUS_AWAITING = "awaiting_human_review"
# Stable runner execution statuses permitted in TerminalProjection / public read
VALID_EXECUTION_STATUSES = frozenset(
    {"completed", "failed", "cancelled", "partial", "unknown"}
)
# Stable sanitized reason codes that may appear in public projection
STABLE_REASON_CODES = frozenset(
    {
        "ALL_CHECKS_PASSED",
        "REQUIRED_CHECK_FAILED",
        "OPTIONAL_CHECK_FAILED",
        "EVIDENCE_INCOMPLETE",
        "EVIDENCE_SCHEMA_INVALID",
        "EVIDENCE_OUT_OF_SCOPE",
        "CRITERION_NOT_CHECKED",
        "CHECK_PASSED",
        "CHECK_FAILED",
        "CHECK_INCONCLUSIVE",
        "COMPLETION_CLAIM_CONTRADICTED",
        "COMPLETION_CLAIM_UNSUPPORTED",
        "PARTIAL_DECLARATION",
        "FLOW_STORE_ERROR",
        "FLOW_VERIFICATION_MISSING",
        "FLOW_RUNNER_FAILURE",
    }
)


class TaskValidationError(ValueError):
    """A submissão falhou validação; carrega um código de falha sanitizado."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class TaskIdempotencyConflict(Exception):
    """Mesma idempotency_key com um `task` canonicamente diferente."""


class TaskStoreUnavailable(Exception):
    """A store durável não pôde processar a operação; nunca expõe detalhe interno."""


class TaskHandleNotFound(Exception):
    """Handle não encontrado; abstenção antes do runner."""

    reason_code: str = "TASK_HANDLE_NOT_FOUND"


class TaskNotExecutable(Exception):
    """Handle está em estado que impede execução (running, terminal ou ausente)."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class TaskSubmission:
    """Tarefa já validada e canonicalizada, pronta para persistência."""

    idempotency_key: str
    task_type: str
    canonical_json: str
    priority: int


@dataclass(frozen=True, slots=True)
class SubmitTaskResult:
    """Resultado sanitizado de uma submissão, criada ou repetida."""

    task_handle: str
    state: str
    created: bool
    revision: int
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class TaskRecord:
    """Projeção pública de uma tarefa armazenada, sem payload interno."""

    task_handle: str
    task_type: str
    state: str
    priority: int
    revision: int
    created_at: str
    updated_at: str
    # FLOW-1 optional projection fields — None when no FLOW cycle has completed
    execution_id: str | None = None
    execution_status: str | None = None
    validation_status: str | None = None
    delivery_status: str | None = None
    chronos_action: str | None = None
    attempts_used: int | None = None
    reason_codes: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class TerminalProjection:
    """Projeção sanitizada validada antes de escrita durável.

    Todos os campos são validados em __post_init__ para garantir que
    somente valores estáveis e permitidos entrem na store.
    """

    execution_id: str
    execution_status: str           # estado do runner (string livre, não ecoada raw)
    validation_status: str          # deve estar em VALID_VALIDATION_STATUSES
    delivery_status: str            # deve ser DELIVERY_STATUS_AWAITING
    chronos_action: str             # deve estar em VALID_CHRONOS_ACTIONS
    attempts_used: int              # >= 1
    reason_codes: tuple[str, ...]   # somente códigos de STABLE_REASON_CODES

    def __post_init__(self) -> None:
        if not isinstance(self.execution_id, str) or not self.execution_id:
            raise ValueError("execution_id must be a non-empty string")
        if self.execution_status not in VALID_EXECUTION_STATUSES:
            raise ValueError(
                f"invalid execution_status: {self.execution_status!r}"
            )
        if type(self.attempts_used) is not int or self.attempts_used < 1:
            raise ValueError("attempts_used must be a positive integer")
        if self.validation_status not in VALID_VALIDATION_STATUSES:
            raise ValueError(
                f"invalid validation_status: {self.validation_status!r}"
            )
        if self.delivery_status != DELIVERY_STATUS_AWAITING:
            raise ValueError(
                f"delivery_status must be {DELIVERY_STATUS_AWAITING!r}"
            )
        if self.chronos_action not in VALID_CHRONOS_ACTIONS:
            raise ValueError(f"invalid chronos_action: {self.chronos_action!r}")
        if not isinstance(self.reason_codes, tuple) or not self.reason_codes:
            raise ValueError("reason_codes must be a non-empty tuple")
        for code in self.reason_codes:
            if code not in STABLE_REASON_CODES:
                raise ValueError(f"unstable reason_code: {code!r}")


@runtime_checkable
class TaskStoreContract(Protocol):
    """Superfície durável consumida pelo servidor MCP, sem SQL vazando acima."""

    def submit_task(self, submission: TaskSubmission) -> SubmitTaskResult:
        """Persistir de forma idempotente e retornar o handle convergido."""
        ...

    def get_task(self, task_handle: str) -> TaskRecord | None:
        """Buscar a projeção sanitizada de uma tarefa, ou None se ausente."""
        ...

    def transition_queued_to_running(
        self, task_handle: str, execution_id: str
    ) -> None:
        """Transição atômica queued→running.

        Lança TaskHandleNotFound se handle ausente.
        Lança TaskNotExecutable se estado atual não for queued.
        Lança TaskStoreUnavailable em falha de infraestrutura.
        """
        ...

    def persist_terminal_projection(
        self, task_handle: str, projection: TerminalProjection
    ) -> None:
        """Gravar projeção terminal sanitizada de forma atômica.

        Requer state='running' AND execution_id correspondente; caso contrário
        faz rollback e lança TaskStoreUnavailable com razão estável.
        Lança TaskStoreUnavailable em qualquer falha de infraestrutura.
        """
        ...

    def close_failed(
        self, task_handle: str, execution_id: str, reason_codes: tuple[str, ...]
    ) -> None:
        """Fechar durável em awaiting_human_review após falha de runner/routing.

        Idempotente: se já terminal, não faz nada. Se state='running' e
        execution_id coincide, transiciona para terminal de falha.
        Lança TaskStoreUnavailable em falha de infraestrutura.
        """
        ...
