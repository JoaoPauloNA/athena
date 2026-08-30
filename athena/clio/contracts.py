"""Contrato CLIO-0 — eventos sanitizados, níveis e limites."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

SCHEMA_VERSION = "clio.event.v1"

LEVEL_NONE = "none"
LEVEL_TECHNICAL = "technical"
LEVEL_PARTIAL = "partial"
LEVEL_COMPLETE = "complete"

VALID_LEVELS = frozenset({LEVEL_NONE, LEVEL_TECHNICAL, LEVEL_PARTIAL, LEVEL_COMPLETE})

LEVEL_RANK: dict[str, int] = {
    LEVEL_NONE: 0,
    LEVEL_TECHNICAL: 1,
    LEVEL_PARTIAL: 2,
    LEVEL_COMPLETE: 3,
}

RETENTION_DAYS: dict[str, int] = {
    LEVEL_COMPLETE: 7,
    LEVEL_PARTIAL: 30,
    LEVEL_TECHNICAL: 90,
    LEVEL_NONE: 0,
}

DEFAULT_LEVEL = LEVEL_TECHNICAL

QUEUE_CAPACITY = 256
BATCH_SIZE = 32
MAX_FLUSH_EVENTS = 512
MAX_TEXT_BYTES = 512
MAX_EVENT_BYTES = 8_192
MAX_REASON_CODES = 16
MAX_FIELD_LEN = 256
FLUSH_INTERVAL_S = 0.5
SHUTDOWN_TIMEOUT_S = 2.0
WRITER_JOIN_TIMEOUT_S = 2.0
MAX_RETIRED_GENERATIONS = 2

ENV_GLOBAL_LEVEL = "ATHENA_CLIO_LEVEL"
ENV_PROJECT_LEVEL = "ATHENA_CLIO_PROJECT_LEVEL"
ENV_USER_LEVEL = "ATHENA_CLIO_USER_LEVEL"
ENV_SECURITY_LEVEL = "ATHENA_CLIO_SECURITY_LEVEL"

TECHNICAL_FIELDS = frozenset(
    {
        "schema_version",
        "event_type",
        "level",
        "timestamp",
        "task_handle",
        "execution_id",
        "tool",
        "provider",
        "model",
        "execution_status",
        "validation_status",
        "delivery_status",
        "chronos_action",
        "attempts_used",
        "duration_ms",
        "queue_wait_ms",
        "timeout_ms",
        "retry_count",
        "error_code",
        "reason_codes",
        "old_level",
        "new_level",
    }
)

PARTIAL_FIELDS = frozenset(
    {
        "request_summary",
        "constraints_summary",
        "decision_summary",
        "result_summary",
    }
)

COMPLETE_FIELDS = frozenset({"protected_envelope"})

FORBIDDEN_FIELD_NAMES = frozenset(
    {
        "prompt",
        "command",
        "argv",
        "cwd",
        "stdout",
        "stderr",
        "env",
        "environment",
        "output",
        "response",
        "message",
        "text",
        "input",
        "arguments",
        "token",
        "api_key",
        "secret",
        "credential",
        "password",
        "authorization",
        "raw_url",
    }
)

VALID_EVENT_TYPES = frozenset(
    {
        "flow.task.started",
        "flow.task.finished",
        "clio.level_changed",
    }
)


@dataclass(frozen=True, slots=True)
class ClioCounters:
    """Contadores sanitizados — nunca expõem conteúdo de tarefa."""

    enqueued: int = 0
    dropped_queue_full: int = 0
    dropped_invalid: int = 0
    dropped_complete_unavailable: int = 0
    writer_failures: int = 0
    retention_failures: int = 0
    none_bypass: int = 0
    dropped_writer_capacity: int = 0


@dataclass
class MutableClioCounters:
    enqueued: int = 0
    dropped_queue_full: int = 0
    dropped_invalid: int = 0
    dropped_complete_unavailable: int = 0
    writer_failures: int = 0
    retention_failures: int = 0
    none_bypass: int = 0
    dropped_writer_capacity: int = 0

    def snapshot(self) -> ClioCounters:
        return ClioCounters(
            enqueued=self.enqueued,
            dropped_queue_full=self.dropped_queue_full,
            dropped_invalid=self.dropped_invalid,
            dropped_complete_unavailable=self.dropped_complete_unavailable,
            writer_failures=self.writer_failures,
            retention_failures=self.retention_failures,
            none_bypass=self.none_bypass,
            dropped_writer_capacity=self.dropped_writer_capacity,
        )


@dataclass(frozen=True, slots=True)
class LevelContext:
    """Entradas para resolução de nível — precedência interna CLIO-0."""

    mcp_suggestion: str | None = None
    user_level: str | None = None
    project_level: str | None = None
    global_level: str | None = None
    security_level: str | None = None


@dataclass(frozen=True, slots=True)
class ProtectedEnvelope:
    """Envelope opaco produzido por protetor aprovado — nunca plaintext."""

    algorithm: str
    payload_b64: str
    key_id: str = ""


@runtime_checkable
class ContentProtectorContract(Protocol):
    """Protetor injetado — único caminho para conteúdo em nível complete."""

    def protect(self, plaintext: bytes) -> ProtectedEnvelope:
        """Transformar bytes em envelope opaco; nunca persiste plaintext."""
        ...


@dataclass(frozen=True, slots=True)
class TechnicalEvent:
    """Evento técnico allowlisted — sem conteúdo de tarefa."""

    event_type: str
    timestamp: str
    task_handle: str = ""
    execution_id: str = ""
    tool: str = ""
    provider: str = ""
    model: str = ""
    execution_status: str = ""
    validation_status: str = ""
    delivery_status: str = ""
    chronos_action: str = ""
    attempts_used: int | None = None
    duration_ms: int | None = None
    queue_wait_ms: int | None = None
    timeout_ms: int | None = None
    retry_count: int | None = None
    error_code: str = ""
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    old_level: str = ""
    new_level: str = ""


@dataclass(frozen=True, slots=True)
class PartialSummaries:
    request_summary: str = ""
    constraints_summary: str = ""
    decision_summary: str = ""
    result_summary: str = ""
