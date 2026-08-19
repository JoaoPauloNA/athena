"""Escalada ao orquestrador após falhas verificáveis consecutivas em run_combo.

Rastreia tentativas com assinatura de erro por execution_id, emite o evento
estruturado ``orchestrator_escalation_required`` e bloqueia loops autônomos
via circuit breaker por assinatura e teto configurável de tentativas.

O orquestrador externo decide continuação; Athena só produz o pacote de
escalada e aceita ``orchestrator_continuation`` explícito em run_combo.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from athena.flight_recorder import FlightRecorder, resolve_logs_dir
from athena.reliability import _categorize_reason

DISTINCT_PROVIDER_THRESHOLD = max(
    1, int(os.environ.get("ATHENA_ESCALATION_DISTINCT_THRESHOLD", "3"))
)
MAX_FAILURE_ATTEMPTS = max(
    1, int(os.environ.get("ATHENA_ESCALATION_MAX_ATTEMPTS", "6"))
)
CIRCUIT_BREAKER_SIGNATURE_THRESHOLD = max(
    1, int(os.environ.get("ATHENA_ESCALATION_CIRCUIT_BREAKER", "2"))
)

_FAILURE_KINDS = frozenset(
    {
        "timeout",
        "empty_output",
        "unconfirmed_termination",
        "provider_error",
        "false_verdict",
    }
)

_LOCK = threading.Lock()
_TRACKERS: dict[str, EscalationTracker] = {}


@dataclass
class FailureAttempt:
    provider_id: str
    attempt_id: str | None
    kind: str
    error_signature: str
    normalized_error: dict[str, Any]
    flight_recorder_refs: dict[str, Any]
    recorded_at_monotonic: float = field(default_factory=time.monotonic)


@dataclass
class EscalationTracker:
    execution_id: str
    failures: list[FailureAttempt] = field(default_factory=list)
    signature_counts: dict[str, int] = field(default_factory=dict)
    distinct_providers: set[str] = field(default_factory=set)
    escalated: bool = False

    def record(self, attempt: FailureAttempt) -> None:
        self.failures.append(attempt)
        self.signature_counts[attempt.error_signature] = (
            self.signature_counts.get(attempt.error_signature, 0) + 1
        )
        self.distinct_providers.add(attempt.provider_id)

    def circuit_breaker_tripped(self) -> bool:
        return any(
            count >= CIRCUIT_BREAKER_SIGNATURE_THRESHOLD
            for count in self.signature_counts.values()
        )

    def should_escalate(self) -> bool:
        if self.escalated:
            return True
        if len(self.failures) >= MAX_FAILURE_ATTEMPTS:
            return True
        if self.circuit_breaker_tripped():
            return True
        if len(self.distinct_providers) >= DISTINCT_PROVIDER_THRESHOLD:
            return True
        return False

    def escalation_reason(self) -> str:
        if self.circuit_breaker_tripped():
            return "circuit_breaker_signature"
        if len(self.failures) >= MAX_FAILURE_ATTEMPTS:
            return "max_failure_attempts"
        if len(self.distinct_providers) >= DISTINCT_PROVIDER_THRESHOLD:
            return "distinct_provider_threshold"
        return "escalation_policy"


def _reset_for_tests() -> None:
    with _LOCK:
        _TRACKERS.clear()


def get_tracker(execution_id: str) -> EscalationTracker:
    with _LOCK:
        tracker = _TRACKERS.get(execution_id)
        if tracker is None:
            tracker = EscalationTracker(execution_id=execution_id)
            _TRACKERS[execution_id] = tracker
        return tracker


def reset_tracker(execution_id: str) -> None:
    with _LOCK:
        _TRACKERS.pop(execution_id, None)


def apply_orchestrator_continuation(
    execution_id: str,
    continuation: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Valida e aplica instrução explícita de continuação do orquestrador."""
    if not continuation:
        return None
    action = str(continuation.get("action") or "").strip().lower()
    if action not in {"retry", "continue"}:
        raise ValueError(
            "orchestrator_continuation.action deve ser 'retry' ou 'continue'"
        )
    if continuation.get("reset_failure_tracker", True):
        reset_tracker(execution_id)
    chain_override = continuation.get("chain_provider_ids")
    if chain_override is not None:
        if not isinstance(chain_override, list) or not chain_override:
            raise ValueError(
                "orchestrator_continuation.chain_provider_ids deve ser lista não vazia"
            )
        normalized = [str(item).strip() for item in chain_override if str(item).strip()]
        if not normalized:
            raise ValueError(
                "orchestrator_continuation.chain_provider_ids inválida"
            )
        return {"chain_provider_ids": normalized}
    return {}


def _normalize_signature(kind: str, detail: str) -> str:
    category = _categorize_reason(detail)
    digest = hashlib.sha256(f"{kind}:{category}:{detail[:120]}".encode()).hexdigest()[:12]
    return f"{kind}:{category}:{digest}"


def _flight_recorder_refs(
    execution_id: str,
    attempt_id: str | None,
    *,
    logs_dir: str | None = None,
) -> dict[str, Any]:
    base = str(resolve_logs_dir())
    refs: dict[str, Any] = {
        "logs_dir": logs_dir or base,
        "execution_id": execution_id,
        "attempt_id": attempt_id,
    }
    if attempt_id:
        artifact_root = f"{base}/artifacts/{execution_id}/{attempt_id}"
        refs["stdout_artifact"] = f"{artifact_root}/stdout.txt"
        refs["stderr_artifact"] = f"{artifact_root}/stderr.txt"
    return refs


def classify_verifiable_failure(
    *,
    provider_id: str,
    result: Any,
    last_error: str | None = None,
    false_verdict: bool = False,
    unconfirmed_termination: bool = False,
    execution_id: str | None = None,
) -> FailureAttempt | None:
    """Classifica falha verificável e devolve tentativa com assinatura normalizada."""
    execution = getattr(result, "execution", None) or {}
    attempt_id = execution.get("attempt_id") if isinstance(execution, dict) else None
    resolved_execution_id = execution_id or (
        execution.get("execution_id") if isinstance(execution, dict) else None
    )

    kind: str | None = None
    detail = (last_error or getattr(result, "error", None) or "").strip()

    if false_verdict:
        kind = "false_verdict"
        detail = detail or "relatorio_falso"
    elif unconfirmed_termination:
        kind = "unconfirmed_termination"
        detail = detail or "terminacao_nao_confirmada"
    elif getattr(result, "timed_out", False):
        kind = "timeout"
        detail = detail or "timeout"
    elif not detail and not (getattr(result, "output", None) or "").strip():
        kind = "empty_output"
        detail = "output_vazio"
    elif getattr(result, "exit_code", 0) != 0:
        kind = "provider_error"
        detail = detail or f"exit_{getattr(result, 'exit_code', 1)}"
    elif not (getattr(result, "output", None) or "").strip():
        kind = "empty_output"
        detail = "output_vazio"

    if kind is None or kind not in _FAILURE_KINDS:
        return None

    signature = _normalize_signature(kind, detail)
    normalized_error = {
        "kind": kind,
        "reason_code": _categorize_reason(detail),
        "message_redacted": detail[:240],
        "exit_code": getattr(result, "exit_code", None),
        "timed_out": bool(getattr(result, "timed_out", False)),
        "execution_state": execution.get("state") if isinstance(execution, dict) else None,
    }
    return FailureAttempt(
        provider_id=provider_id,
        attempt_id=attempt_id,
        kind=kind,
        error_signature=signature,
        normalized_error=normalized_error,
        flight_recorder_refs=_flight_recorder_refs(
            resolved_execution_id or "",
            attempt_id,
        ),
    )


def record_failure_and_maybe_escalate(
    *,
    execution_id: str,
    combo_id: str,
    attempted_chain: list[str],
    failure: FailureAttempt,
    budget_remaining_s: float | None = None,
    episode_id: str | None = None,
) -> dict[str, Any] | None:
    """Registra falha; retorna pacote de escalada se política exigir."""
    tracker = get_tracker(execution_id)
    tracker.record(failure)
    if not tracker.should_escalate():
        return None
    return build_escalation_package(
        tracker=tracker,
        combo_id=combo_id,
        attempted_chain=attempted_chain,
        budget_remaining_s=budget_remaining_s,
        episode_id=episode_id or execution_id,
    )


def build_escalation_package(
    *,
    tracker: EscalationTracker,
    combo_id: str,
    attempted_chain: list[str],
    budget_remaining_s: float | None,
    episode_id: str,
) -> dict[str, Any]:
    tracker.escalated = True
    failures_payload = [
        {
            "provider_id": item.provider_id,
            "attempt_id": item.attempt_id,
            "kind": item.kind,
            "error_signature": item.error_signature,
            "normalized_error": item.normalized_error,
            "flight_recorder_refs": item.flight_recorder_refs,
        }
        for item in tracker.failures
    ]
    package = {
        "event_type": "orchestrator_escalation_required",
        "status": "awaiting_orchestrator",
        "execution_id": tracker.execution_id,
        "episode_id": episode_id,
        "combo_id": combo_id,
        "escalation_reason": tracker.escalation_reason(),
        "distinct_providers_failed": len(tracker.distinct_providers),
        "failure_count": len(tracker.failures),
        "attempted_chain": list(attempted_chain),
        "consecutive_failures": failures_payload,
        "normalized_errors": [item["normalized_error"] for item in failures_payload],
        "error_signatures": [item.error_signature for item in tracker.failures],
        "circuit_breaker_tripped": tracker.circuit_breaker_tripped(),
        "budget_remaining_s": budget_remaining_s,
        "thresholds": {
            "distinct_provider_threshold": DISTINCT_PROVIDER_THRESHOLD,
            "max_failure_attempts": MAX_FAILURE_ATTEMPTS,
            "circuit_breaker_signature_threshold": CIRCUIT_BREAKER_SIGNATURE_THRESHOLD,
        },
        "continuation_hint": {
            "action_required": "orchestrator_decision",
            "resume_with": "orchestrator_continuation em run_combo com o mesmo execution_id",
            "no_autonomous_loop": True,
        },
        "summary": (
            f"Combo '{combo_id}' requer decisão do orquestrador após "
            f"{len(tracker.distinct_providers)} provider(s) distintos com falha."
        ),
    }
    emit_orchestrator_escalation_event(package)
    return package


def emit_orchestrator_escalation_event(package: dict[str, Any]) -> None:
    execution_id = str(package.get("execution_id") or uuid.uuid4().hex)
    attempt_id = f"escalation-{uuid.uuid4().hex[:12]}"
    recorder = FlightRecorder(
        execution_id=execution_id,
        attempt_id=attempt_id,
        provider="router",
        profile="combo",
        transport="local",
    )
    recorder.record_event(
        "orchestrator_escalation_required",
        status=package.get("status"),
        episode_id=package.get("episode_id"),
        combo_id=package.get("combo_id"),
        escalation_reason=package.get("escalation_reason"),
        distinct_providers_failed=package.get("distinct_providers_failed"),
        failure_count=package.get("failure_count"),
        attempted_chain=package.get("attempted_chain"),
        consecutive_failures=package.get("consecutive_failures"),
        normalized_errors=package.get("normalized_errors"),
        error_signatures=package.get("error_signatures"),
        circuit_breaker_tripped=package.get("circuit_breaker_tripped"),
        budget_remaining_s=package.get("budget_remaining_s"),
        continuation_hint=package.get("continuation_hint"),
    )
