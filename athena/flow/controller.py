"""Implementação concreta do FlowController (FLOW-1).

Compõe:
 - athena.tasks: transição queued→running, persistência terminal, close_failed.
 - athena.evidence_gate: validação determinística do envelope sanitizado.
 - athena.chronos: decisão de ciclo (CLOSED / HUMAN_REVIEW).

Evidence Gate é o único emissor de veredito. Os critérios são construídos
por índice (nunca por finding.subject), passados como acceptance_criteria
para que o motor EG compute cobertura e consistência de forma determinística.

Completed + todos os checks passaram => EG PASS => Chronos CLOSED.
Execução falha ou verificação fail/inconclusive => EG FAIL/INCONCLUSIVE => HUMAN_REVIEW.

Nunca importado pelo mcp_server — injetado pelo mcp_runtime.
"""

from __future__ import annotations

from typing import Any

from athena.chronos import ChronosCycle, CycleAttempt
from athena.evidence_gate import evaluate_result
from athena.tasks import (
    DELIVERY_STATUS_AWAITING,
    STABLE_REASON_CODES,
    TaskStoreContract,
    TaskStoreUnavailable,
    TerminalProjection,
)
from athena.tasks.contracts import VALID_EXECUTION_STATUSES

from .contracts import FlowControllerContract

# Delivery status invariant (EG-3A)
_DELIVERY_STATUS = DELIVERY_STATUS_AWAITING

_VALIDATION_BY_VERDICT: dict[str, str] = {
    "PASS": "pass",
    "FAIL": "fail",
    "INCONCLUSIVE": "inconclusive",
    "ESCALATE": "escalate",
}

# Criterion IDs for the FLOW-1 envelope — stable, index-based
_CRIT_EXEC = "criterion:exec:0"
_CRIT_VERIFY = "criterion:verify:phase"


def _crit_det(idx: int) -> str:
    return f"criterion:det:{idx}"


def _deterministic_phase_passed(verification_result: Any) -> bool:
    """True only when the deterministic verifier phase objectively passed."""
    if verification_result is None:
        return False
    try:
        det = verification_result.deterministic
    except Exception:  # noqa: BLE001
        return False
    try:
        return det.passed is True
    except Exception:  # noqa: BLE001
        return False


def _safe_execution_status(raw_state: str) -> str:
    return raw_state if raw_state in VALID_EXECUTION_STATUSES else "unknown"


def _build_envelope_and_criteria(
    execution_id: str,
    execution_result: Any,
    verification_result: Any,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build sanitized EG envelope and matching acceptance_criteria list.

    Criteria are identified by stable index, never by finding.subject.
    Returns (envelope, acceptance_criteria).
    """
    # 1. Execution state (safe attribute only — no raw stdout/stderr/env)
    exec_state = "unknown"
    try:
        state_attr = getattr(execution_result, "state", None)
        if state_attr is not None:
            exec_state = str(getattr(state_attr, "value", state_attr))
    except Exception:  # noqa: BLE001, S110
        pass

    declared_status = (
        exec_state
        if exec_state in ("completed", "failed", "cancelled", "partial")
        else "unknown"
    )

    # 2. Build execution, verification-phase, and per-finding criteria
    checks: list[dict[str, Any]] = []
    acceptance_criteria: list[dict[str, Any]] = []

    # Execution criterion: pass iff runner state is "completed"
    exec_status = "pass" if exec_state == "completed" else "fail"
    acceptance_criteria.append({"id": _CRIT_EXEC, "required": True})
    checks.append({
        "criterion_id": _CRIT_EXEC,
        "status": exec_status,
        "evidence_refs": ["evidence/execution"],
    })

    # Verification-phase criterion: pass only when deterministic phase passed
    verify_status = (
        "pass" if _deterministic_phase_passed(verification_result) else "fail"
    )
    acceptance_criteria.append({"id": _CRIT_VERIFY, "required": True})
    checks.append({
        "criterion_id": _CRIT_VERIFY,
        "status": verify_status,
        "evidence_refs": ["evidence/verifier-phase"],
    })

    # Deterministic finding criteria (index-based, no subject in criterion_id)
    if verification_result is not None:
        try:
            det = verification_result.deterministic
            for idx, finding in enumerate(det.findings):
                cid = _crit_det(idx)
                status_val = str(
                    getattr(finding.status, "value", finding.status)
                )
                check_status = "pass" if status_val == "passed" else "fail"
                acceptance_criteria.append({"id": cid, "required": True})
                checks.append({
                    "criterion_id": cid,
                    "status": check_status,
                    "evidence_refs": ["evidence/verifier"],
                })
        except Exception:  # noqa: BLE001, S110
            pass

    envelope: dict[str, Any] = {
        "schema_version": "0.1",
        "task_id": f"flow:{execution_id[:8]}",
        "attempt_id": execution_id,
        "declared_status": declared_status,
        "claims": [],
        "checks": checks,
        "artifacts": [],
        "telemetry": {"executor_id": "flow1"},
    }
    return envelope, acceptance_criteria


def _sanitize_reason_codes(codes: tuple[str, ...]) -> tuple[str, ...]:
    """Keep only stable codes; replace unknown with sentinel, deduplicate."""
    result = []
    for code in codes:
        result.append(code if code in STABLE_REASON_CODES else "FLOW_STORE_ERROR")
    return tuple(dict.fromkeys(result)) or ("FLOW_STORE_ERROR",)


def _merge_public_reason_codes(
    input_codes: tuple[str, ...],
    gate_codes: tuple[str, ...],
) -> tuple[str, ...]:
    """Stable input failure codes first, then sanitized Evidence Gate codes."""
    merged = list(_sanitize_reason_codes(input_codes))
    for code in _sanitize_reason_codes(gate_codes):
        if code not in merged:
            merged.append(code)
    return tuple(merged) or ("FLOW_STORE_ERROR",)


def _build_failure_envelope(
    execution_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Fixed sanitized failure envelope: required exec + verify criteria fail."""
    acceptance_criteria = [
        {"id": _CRIT_EXEC, "required": True},
        {"id": _CRIT_VERIFY, "required": True},
    ]
    checks = [
        {
            "criterion_id": _CRIT_EXEC,
            "status": "fail",
            "evidence_refs": ["evidence/execution"],
        },
        {
            "criterion_id": _CRIT_VERIFY,
            "status": "fail",
            "evidence_refs": ["evidence/verifier-phase"],
        },
    ]
    envelope: dict[str, Any] = {
        "schema_version": "0.1",
        "task_id": f"flow:{execution_id[:8]}",
        "attempt_id": execution_id,
        "declared_status": "failed",
        "claims": [],
        "checks": checks,
        "artifacts": [],
        "telemetry": {"executor_id": "flow1"},
    }
    return envelope, acceptance_criteria


class FlowController:
    """Concrete FlowController — injected by mcp_runtime into MCPServer."""

    def __init__(self, task_store: TaskStoreContract) -> None:
        self._store = task_store

    def begin(self, task_handle: str, execution_id: str) -> None:
        """Atomic queued→running transition. Raises on any non-queued state."""
        self._store.transition_queued_to_running(task_handle, execution_id)

    def _finalize_through_gate(
        self,
        *,
        task_handle: str,
        execution_id: str,
        envelope: dict[str, Any],
        acceptance_criteria: list[dict[str, Any]],
        execution_status: str,
        input_reason_codes: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Evidence Gate (sole verdict emitter) + Chronos + durable persistence."""
        gate_verdict = evaluate_result(
            envelope, acceptance_criteria=acceptance_criteria
        )
        validation_status = _VALIDATION_BY_VERDICT.get(
            gate_verdict.verdict, "inconclusive"
        )

        expected_criteria = tuple(c["id"] for c in acceptance_criteria)
        cycle = ChronosCycle(
            cycle_id=f"flow1:{execution_id[:8]}",
            expected_criteria=expected_criteria,
        )
        attempt = CycleAttempt(
            attempt_number=1,
            verdict=gate_verdict.verdict,
            in_scope=True,
            new_exit_criteria_written=False,
        )
        chronos_result = cycle.record(attempt)
        chronos_action = chronos_result["action"]
        attempts_used = chronos_result["attempts_used"]
        if chronos_action != "CLOSED":
            chronos_action = "HUMAN_REVIEW"

        safe_codes = _merge_public_reason_codes(
            input_reason_codes,
            gate_verdict.reason_codes or ("FLOW_STORE_ERROR",),
        )

        projection = TerminalProjection(
            execution_id=execution_id,
            execution_status=_safe_execution_status(execution_status),
            validation_status=validation_status,
            delivery_status=_DELIVERY_STATUS,
            chronos_action=chronos_action,
            attempts_used=attempts_used,
            reason_codes=safe_codes,
        )
        self._store.persist_terminal_projection(task_handle, projection)

        return {
            "execution_id": execution_id,
            "validation_status": validation_status,
            "delivery_status": _DELIVERY_STATUS,
            "chronos_action": chronos_action,
            "attempts_used": attempts_used,
            "reason_codes": list(safe_codes),
        }

    def finish(
        self,
        task_handle: str,
        execution_id: str,
        execution_result: Any,
        verification_result: Any,
    ) -> dict[str, Any]:
        """Evidence Gate (sole verdict emitter) + Chronos + durable persistence."""
        envelope, acceptance_criteria = _build_envelope_and_criteria(
            execution_id, execution_result, verification_result
        )

        exec_state = "unknown"
        try:
            state_attr = getattr(execution_result, "state", None)
            if state_attr is not None:
                exec_state = str(getattr(state_attr, "value", state_attr))
        except Exception:  # noqa: BLE001, S110
            pass

        return self._finalize_through_gate(
            task_handle=task_handle,
            execution_id=execution_id,
            envelope=envelope,
            acceptance_criteria=acceptance_criteria,
            execution_status=exec_state,
        )

    def close_failed(
        self,
        task_handle: str,
        execution_id: str,
        reason_codes: tuple[str, ...],
    ) -> None:
        """Durably close to awaiting_human_review after routing/runner/verifier failure.

        Evidence Gate is the sole verdict emitter; Chronos records the attempt.
        Never persists exception text or raw subprocess data.
        Idempotent: if already terminal with same execution_id, does nothing.
        """
        record = self._store.get_task(task_handle)
        if record is None:
            raise TaskStoreUnavailable("close_failed: handle not found")
        if record.state == "awaiting_human_review":
            if record.execution_id == execution_id:
                return
            raise TaskStoreUnavailable(
                "close_failed: already terminal with different execution_id"
            )
        if record.state != "running":
            raise TaskStoreUnavailable("close_failed: task not running")
        if record.execution_id != execution_id:
            raise TaskStoreUnavailable("close_failed: execution_id mismatch")

        envelope, acceptance_criteria = _build_failure_envelope(execution_id)
        self._finalize_through_gate(
            task_handle=task_handle,
            execution_id=execution_id,
            envelope=envelope,
            acceptance_criteria=acceptance_criteria,
            execution_status="failed",
            input_reason_codes=reason_codes,
        )


# Module-level factory used by mcp_runtime
def make_flow_controller(task_store: TaskStoreContract) -> FlowControllerContract:
    """Factory used by mcp_runtime to compose the concrete FlowController."""
    return FlowController(task_store)
