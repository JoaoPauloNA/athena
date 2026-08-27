"""EG-3A: pipeline interno opt-in de finalização de artefato.

Decisão canônica (Vault, Evidence-Gate-Integracao-EG3-EG4):
- interno, posterior à execução; NÃO altera as cinco tools MCP públicas;
- EG-4A é advisory e NUNCA emite nem converte veredito em PASS;
- estado final do artefato: delivery_status = awaiting_human_review;
- três dimensões independentes (execution/validation/delivery).

Este módulo consome o motor EG-1 (evaluate_result) — nada de tool pública nova.
"""

from __future__ import annotations

from typing import Any

from athena.evidence_gate.engine import evaluate_result

FINAL_DELIVERY_STATUS = "awaiting_human_review"

_VALIDATION_BY_VERDICT = {
    "PASS": "pass",
    "FAIL": "fail",
    "INCONCLUSIVE": "inconclusive",
    "ESCALATE": "escalate",
}


def finalize_artifact(envelope: dict[str, Any], *,
                      opt_in: bool = False,
                      acceptance_criteria: list[dict[str, Any]] | None = None,
                      eg4_advisory: str | None = None,
                      ) -> dict[str, Any]:
    """Validar o Result Envelope do artefato e prepará-lo para revisão humana.

    Sem `opt_in=True`, o pipeline não roda (comportamento default intacto).
    EG-4A (`eg4_advisory`) pode apenas ANEXAR um parecer textual quando a
    validação não passou — jamais altera o veredito nem o status.
    """
    if not opt_in:
        return {"pipeline": "eg3a", "ran": False}

    verdict = evaluate_result(envelope, acceptance_criteria=acceptance_criteria)
    validation_status = _VALIDATION_BY_VERDICT[verdict.verdict]

    result: dict[str, Any] = {
        "pipeline": "eg3a",
        "ran": True,
        "execution_status": envelope.get("declared_status", "unknown"),
        "validation_status": validation_status,
        # invariante canônico: o pipeline sempre termina em revisão humana
        "delivery_status": FINAL_DELIVERY_STATUS,
        "reason_codes": list(verdict.reason_codes),
        "evidence_coverage": verdict.evidence_coverage,
        "next_action": verdict.next_action,
    }

    if eg4_advisory is not None and validation_status != "pass":
        # advisory: texto limitado, sem poder de mudar qualquer status
        result["eg4_advisory"] = str(eg4_advisory)[:500]
        result["reason_codes"] = result["reason_codes"] + ["EG4_ADVISORY_ATTACHED"]

    return result
