"""Testes do Chronos: circuit breaker, reabertura governada, escalada."""

from __future__ import annotations

from athena.chronos import ChronosCycle, CycleAttempt


def _cycle():
    return ChronosCycle("c-1", expected_criteria=("c1", "c2"))


def test_pass_fecha_ciclo():
    c = _cycle()
    r = c.record(CycleAttempt(1, "PASS", True, True))
    assert r["action"] == "CLOSED" and r["reason"] == "CRITERIA_MET"


def test_falha_com_novos_criterios_reabre():
    c = _cycle()
    r = c.record(CycleAttempt(1, "FAIL", True, True))
    assert r["action"] == "REOPEN_FOR_CORRECTION"
    assert r["attempt_budget_remaining"] == 2


def test_sem_novos_criterios_escala_humano():
    c = _cycle()
    r = c.record(CycleAttempt(1, "FAIL", True, False))
    assert r["action"] == "HUMAN_REVIEW"
    assert r["reason"] == "NO_NEW_EXIT_CRITERIA"


def test_fora_do_escopo_escala_imediatamente():
    c = _cycle()
    r = c.record(CycleAttempt(1, "FAIL", in_scope=False, new_exit_criteria_written=True))
    assert r["action"] == "HUMAN_REVIEW"
    assert r["reason"] == "ESCALATION_POLICY"


def test_escalate_vai_direto_ao_humano():
    c = _cycle()
    r = c.record(CycleAttempt(1, "ESCALATE", True, True))
    assert r["action"] == "HUMAN_REVIEW"


def test_circuit_breaker_na_terceira_falha():
    c = _cycle()
    for i in (1, 2):
        r = c.record(CycleAttempt(i, "FAIL", True, True))
        assert r["action"] == "REOPEN_FOR_CORRECTION"
    r3 = c.record(CycleAttempt(3, "FAIL", True, True))
    assert r3["action"] == "HUMAN_REVIEW"
    assert r3["reason"] == "CIRCUIT_BREAKER_3_FAILURES"
    # quarta tentativa não é aceita no fluxo normal
    r4 = c.record(CycleAttempt(4, "FAIL", True, True))
    assert r4["action"] == "HUMAN_REVIEW"


def test_summary_reflete_orcamento():
    c = _cycle()
    c.record(CycleAttempt(1, "FAIL", True, True))
    s = c.summary()
    assert s["attempts"] == 1 and s["budget_remaining"] == 2
