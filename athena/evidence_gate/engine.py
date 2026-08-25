"""Evidence Gate offline (EG-1): validador determinístico dos envelopes v0.1.

Consome os schemas do Vault (Task/Result Envelope, Validation Verdict) e a
política automatizada, executando na ordem canônica:
schema -> campos -> cobertura critério↔check -> evidência resolvível ->
regras determinísticas -> consistência status×exit×artefatos.

Saída: veredito PASS | FAIL | INCONCLUSIVE | ESCALATE com reason codes.
Nunca chama modelos; o avaliador por exceção (EG-4) é acionado pelo chamador.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = "0.1"

# precedência canônica da política v0.1
_PRECEDENCE = {"ESCALATE": 0, "FAIL": 1, "INCONCLUSIVE": 2, "PASS": 3}


@dataclass(frozen=True, slots=True)
class GateVerdict:
    verdict: str                       # PASS|FAIL|INCONCLUSIVE|ESCALATE
    reason_codes: tuple[str, ...]
    criterion_results: tuple[dict[str, Any], ...]
    evidence_coverage: float
    evaluator_required: bool
    next_action: str                   # accept|agent_review|human_review|revise
    schema_valid: bool = True

    def __post_init__(self) -> None:
        if self.verdict not in _PRECEDENCE:
            raise ValueError(f"veredito inválido: {self.verdict}")
        if not 0.0 <= self.evidence_coverage <= 1.0:
            raise ValueError("evidence_coverage fora de [0,1]")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "verdict": self.verdict,
            "reason_codes": list(self.reason_codes),
            "criterion_results": list(self.criterion_results),
            "evidence_coverage": round(self.evidence_coverage, 4),
            "evaluator_required": self.evaluator_required,
            "next_action": self.next_action,
            "schema_valid": self.schema_valid,
        }


def _worst(a: str, b: str) -> str:
    return a if _PRECEDENCE[a] <= _PRECEDENCE[b] else b


def _merge(verdicts: list[str]) -> str:
    out = "PASS"
    for v in verdicts:
        out = _worst(out, v)
    return out


def _action_for(verdict: str) -> str:
    return {
        "PASS": "accept",
        "FAIL": "agent_review",
        "INCONCLUSIVE": "agent_review",
        "ESCALATE": "human_review",
    }[verdict]


def evaluate_result(
    result_envelope: dict[str, Any],
    *,
    acceptance_criteria: list[dict[str, Any]] | None = None,
    authorized_evidence_prefixes: tuple[str, ...] = ("evidence/",),
    risk_requires_human: bool = False,
) -> GateVerdict:
    """Validar um Result Envelope contra os critérios da Task Envelope.

    acceptance_criteria: lista de {"id", "required": bool} da task.
    authorized_evidence_prefixes: refs de evidência devem começar com um deles.
    """
    reasons: list[str] = []
    criteria_results: list[dict[str, Any]] = []

    # --- estrutura mínima (schema leve, sem jsonschema para zero-dependência)
    required_fields = ("schema_version", "task_id", "attempt_id",
                       "declared_status", "claims", "checks",
                       "artifacts", "telemetry")
    missing = [f for f in required_fields if f not in result_envelope]
    if missing:
        return GateVerdict("FAIL", ("EVIDENCE_SCHEMA_INVALID",), (), 0.0,
                           True, "agent_review", schema_valid=False)

    checks = result_envelope.get("checks") or []
    checks_by_criterion = {c.get("criterion_id"): c for c in checks}

    # --- cobertura critério↔check
    coverage_hits = 0
    for crit in (acceptance_criteria or []):
        cid = crit["id"]
        required = crit.get("required", True)
        check = checks_by_criterion.get(cid)
        if check is None:
            criteria_results.append({
                "criterion_id": cid, "status": "inconclusive",
                "reason_code": "CRITERION_NOT_CHECKED"})
            continue
        status = check.get("status")
        evidence_refs = check.get("evidence_refs") or []
        # evidência deve ser resolvível dentro do escopo autorizado
        bad_refs = [r for r in evidence_refs
                    if not str(r).startswith(authorized_evidence_prefixes)]
        if bad_refs:
            reasons.append("EVIDENCE_OUT_OF_SCOPE")
            criteria_results.append({
                "criterion_id": cid, "status": "inconclusive",
                "reason_code": "EVIDENCE_OUT_OF_SCOPE"})
            continue
        if status == "pass":
            coverage_hits += 1
            criteria_results.append({"criterion_id": cid, "status": "pass",
                                     "reason_code": "CHECK_PASSED"})
        elif status == "fail":
            reasons.append("REQUIRED_CHECK_FAILED" if required else "OPTIONAL_CHECK_FAILED")
            criteria_results.append({"criterion_id": cid, "status": "fail",
                                     "reason_code": "CHECK_FAILED"})
        else:  # not_run / inconclusive
            reasons.append("EVIDENCE_INCOMPLETE")
            criteria_results.append({"criterion_id": cid, "status": "inconclusive",
                                     "reason_code": "CHECK_INCONCLUSIVE"})

    if acceptance_criteria:
        total = len(acceptance_criteria)
        coverage = coverage_hits / total
    else:
        # sem critérios definidos na tarefa, não há o que cobrir
        total = 0
        coverage = 1.0

    # --- consistência declarado × checks × exit code
    declared = result_envelope.get("declared_status")
    telemetry = result_envelope.get("telemetry") or {}
    exit_code = telemetry.get("exit_code")
    any_fail = any(c["status"] == "fail" for c in criteria_results)
    all_pass = bool(criteria_results) and all(c["status"] == "pass"
                                              for c in criteria_results)

    consistency = "PASS"
    if declared == "completed":
        if any_fail or exit_code not in (0, None):
            consistency = "FAIL"
            reasons.append("COMPLETION_CLAIM_CONTRADICTED")
        elif not all_pass:
            consistency = "INCONCLUSIVE"
            reasons.append("COMPLETION_CLAIM_UNSUPPORTED")
    elif declared in ("failed", "blocked"):
        consistency = "PASS"  # honestidade: falha declarada não é violação
    elif declared == "partial":
        consistency = "INCONCLUSIVE"
        reasons.append("PARTIAL_DECLARATION")

    # --- veredito final pela precedência
    rule_verdict = _merge([
        "PASS" if not missing else "FAIL",
        "FAIL" if any_fail else "PASS",
        "PASS" if coverage >= 1.0 else "INCONCLUSIVE",
        consistency,
        "ESCALATE" if risk_requires_human else "PASS",
    ])

    evaluator_required = rule_verdict in ("FAIL", "INCONCLUSIVE")
    return GateVerdict(
        verdict=rule_verdict,
        reason_codes=tuple(dict.fromkeys(reasons)) or ("ALL_CHECKS_PASSED",),
        criterion_results=tuple(criteria_results),
        evidence_coverage=round(coverage, 4),
        evaluator_required=evaluator_required,
        next_action=_action_for(rule_verdict),
    )
