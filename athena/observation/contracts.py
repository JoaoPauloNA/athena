"""Contrato de observação sombra Athena → Moiras (MO-1, v1.0).

Fronteira única entre o núcleo modular e o laboratório Moiras.

Invariantes:
- O evento carrega APENAS campos allowlisted: contadores, flags e IDs.
  Nunca prompt, comando, stdout/stderr, caminho de workspace ou credencial.
- O consumidor (adapter MO-2) é opt-in via env `ATHENA_MOIRAS_SHADOW=1`.
- Moiras nunca decide, cancela, autoriza fallback nem altera o ciclo de
  vida: este contrato produz apenas snapshots para observação.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

ALLOWED_EVENT_FIELDS = frozenset(
    {
        "execution_id",
        "attempt_id",
        "provider",          # nome declarado da tentativa (não é segredo)
        "state",             # valor de ExecutionState
        "progress_counter",
        "duration_s",
        "expired_deadline",  # "absolute_timeout_s" | "idle_timeout_s" | None
        "cancelled_by_client",
    }
)

FORBIDDEN_FIELD_NAMES = frozenset(
    {
        "prompt",
        "command",
        "argv",
        "cwd",
        "stdout",
        "stderr",
        "env",
        "output",
        "response",
        "message",
        "text",
        "input",
        "arguments",
    }
)


def validate_event_payload(payload: dict) -> None:
    """Falhar alto se o payload sair da allowlist (defesa em profundidade)."""
    extra = set(payload) - ALLOWED_EVENT_FIELDS
    if extra:
        raise ValueError(f"campos fora da allowlist do contrato Moiras: {sorted(extra)}")
    leaked = FORBIDDEN_FIELD_NAMES & set(payload)
    if leaked:
        raise ValueError(f"campos proibidos presentes no evento: {sorted(leaked)}")


@dataclass(frozen=True, slots=True)
class ShadowExecutionEvent:
    """Snapshot sanitizado de um marco do ciclo de vida, pronto para Moiras."""

    execution_id: str
    attempt_id: str
    provider: str
    state: str                    # valor string de ExecutionState
    progress_counter: int
    duration_s: float | None
    expired_deadline: str | None
    cancelled_by_client: bool

    def __post_init__(self) -> None:
        if not self.execution_id or not isinstance(self.execution_id, str):
            raise ValueError("execution_id deve ser string não vazia")
        if not self.attempt_id or not isinstance(self.attempt_id, str):
            raise ValueError("attempt_id deve ser string não vazia")
        if not self.provider or not isinstance(self.provider, str):
            raise ValueError("provider deve ser string não vazia")
        if not isinstance(self.progress_counter, int) or isinstance(self.progress_counter, bool):
            raise TypeError("progress_counter deve ser int")
        if self.progress_counter < 0:
            raise ValueError("progress_counter não pode ser negativo")
        if self.duration_s is not None and (
            not isinstance(self.duration_s, (int, float)) or self.duration_s < 0
        ):
            raise ValueError("duration_s deve ser número >= 0 ou None")
        if not isinstance(self.cancelled_by_client, bool):
            raise TypeError("cancelled_by_client deve ser bool")

    def to_dict(self) -> dict:
        payload = {
            "execution_id": self.execution_id,
            "attempt_id": self.attempt_id,
            "provider": self.provider,
            "state": self.state,
            "progress_counter": self.progress_counter,
            "duration_s": self.duration_s,
            "expired_deadline": self.expired_deadline,
            "cancelled_by_client": self.cancelled_by_client,
        }
        # autodefesa: o próprio contrato valida a si mesmo
        validate_event_payload(payload)
        return payload


class ShadowObserverContract(Protocol):
    """O que um observer sombra pode fazer: receber eventos. Só isso."""

    def observe(self, event: ShadowExecutionEvent) -> None:
        """Consumir um snapshot sanitizado; NUNCA lança para o fluxo principal."""
        ...
