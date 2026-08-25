"""EG-GATE: conjunto adversarial reservado do Evidence Gate.

Casos de desenvolvimento (EG-1) e conjunto RESERVADO (nunca usado para
ajustar o motor) com resultado esperado por oráculo humano-definido.
Zero falso PASS no reservado é critério de fechamento.
"""

from __future__ import annotations

import pytest

from athena.evidence_gate import evaluate_result


def _criteria(*ids, all_required=True):
    return [{"id": i, "required": all_required} for i in ids]


def _envelope(**over):
    base = {
        "schema_version": "0.1",
        "task_id": "t-1",
        "attempt_id": "a-1",
        "declared_status": "completed",
        "claims": [{"id": "c1", "statement": "feito",
                    "evidence_refs": ["evidence/x.txt"]}],
        "checks": [],
        "artifacts": [],
        "telemetry": {"exit_code": 0, "duration_s": 1.0, "executor_id": "e"},
    }
    base.update(over)
    return base


def _check(cid, status="pass", refs=("evidence/a.log",)):
    return {"criterion_id": cid, "status": status,
            "evidence_refs": list(refs)}


# --------------------------------------------------- casos de desenvolvimento


def test_tudo_passando_e_PASS():
    v = evaluate_result(
        _envelope(checks=[_check("c1"), _check("c2")]),
        acceptance_criteria=_criteria("c1", "c2"),
    )
    assert v.verdict == "PASS" and v.next_action == "accept"
    assert not v.evaluator_required
    assert v.evidence_coverage == 1.0


def test_schema_invalido_e_FAIL():
    v = evaluate_result({"schema_version": "0.1"})
    assert v.verdict == "FAIL"
    assert not v.schema_valid
    assert "EVIDENCE_SCHEMA_INVALID" in v.reason_codes


def test_check_falho_em_criterio_obrigatorio_bloqueia_completed():
    v = evaluate_result(
        _envelope(checks=[_check("c1", "fail")]),
        acceptance_criteria=_criteria("c1"),
    )
    assert v.verdict == "FAIL"
    assert "COMPLETION_CLAIM_CONTRADICTED" in v.reason_codes


def test_completado_sem_checks_vira_INCONCLUSIVE():
    v = evaluate_result(_envelope(), acceptance_criteria=_criteria("c1"))
    assert v.verdict == "INCONCLUSIVE"
    assert "COMPLETION_CLAIM_UNSUPPORTED" in v.reason_codes


def test_evidencia_fora_do_escopo_e_INCONCLUSIVE():
    v = evaluate_result(
        _envelope(checks=[_check("c1", refs=("/etc/passwd",))]),
        acceptance_criteria=_criteria("c1"),
    )
    assert v.verdict == "INCONCLUSIVE"
    assert "EVIDENCE_OUT_OF_SCOPE" in v.reason_codes


def test_exit_code_diferente_de_zero_contradiz_completed():
    env = _envelope(telemetry={"exit_code": 2, "duration_s": 1.0,
                               "executor_id": "e"})
    v = evaluate_result(env, acceptance_criteria=_criteria("c1"))
    assert v.verdict == "FAIL"


def test_falha_declarada_honestamente_e_PASS():
    env = _envelope(declared_status="failed")
    v = evaluate_result(env)
    assert v.verdict == "PASS"  # honestidade não é violação


def test_risk_requires_human_forca_ESCALATE():
    v = evaluate_result(
        _envelope(checks=[_check("c1")]),
        acceptance_criteria=_criteria("c1"),
        risk_requires_human=True,
    )
    assert v.verdict == "ESCALATE"
    assert v.next_action == "human_review"
    assert not v.evaluator_required  # escalada humana não gasta avaliador


# ------------------------------------- conjunto RESERVADO (oráculo fixo)


RESERVED_CASES = [
    # (nome, envelope, criteria, kwargs, veredito esperado)
    ("reservado: falso completed com check not_run",
     _envelope(checks=[_check("r1", "not_run")]),
     _criteria("r1"), {}, "INCONCLUSIVE"),
    ("reservado: evidência fora de escopo",
     _envelope(checks=[_check("r1", refs=("http://evil/e",))]),
     _criteria("r1"), {}, "INCONCLUSIVE"),
    ("reservado: exit 1 + claim completed",
     _envelope(telemetry={"exit_code": 1, "duration_s": 2.0, "executor_id": "x"},
               checks=[_check("r1")]),
     _criteria("r1"), {}, "FAIL"),
    ("reservado: tudo íntegro",
     _envelope(checks=[_check("r1"), _check("r2", refs=("evidence/b.log",))]),
     _criteria("r1", "r2"), {}, "PASS"),
]


@pytest.mark.parametrize("name,env,crit,kw,expected", RESERVED_CASES,
                         ids=[c[0] for c in RESERVED_CASES])
def test_conjunto_reservado(name, env, crit, kw, expected):
    v = evaluate_result(env, acceptance_criteria=crit, **kw)
    assert v.verdict == expected, f"falso {expected} detectado: {v.to_dict()}"


def test_gate_zero_falso_pass_no_reservado():
    """Métrica EG-GATE: nenhum caso adversarial do reservado virou PASS."""
    adversarial = [c for c in RESERVED_CASES if c[4] != "PASS"]
    for _, env, crit, kw, expected in adversarial:
        v = evaluate_result(env, acceptance_criteria=crit, **kw)
        assert v.verdict != "PASS", f"FALSO PASS: {v.to_dict()}"
