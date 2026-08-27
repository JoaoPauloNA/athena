"""EG-3A: testes do pipeline interno opt-in de finalização de artefato.

Invariantes canônicos cobertos:
- sem opt-in não roda;
- delivery_status é SEMPRE awaiting_human_review;
- EG-4A advisory nunca converte FAIL/INCONCLUSIVE/ESCALATE em PASS;
- execução completa ≠ validação pass (dimensões independentes);
- cinco tools MCP públicas permanecem intactas (não testado aqui — smoke JSON-RPC).
"""

from __future__ import annotations

from pathlib import Path

from athena.evidence_gate.pipeline_eg3a import FINAL_DELIVERY_STATUS, finalize_artifact


def _envelope(status="completed"):
    return {
        "schema_version": "0.1",
        "task_id": "t-1",
        "attempt_id": "a-1",
        "declared_status": status,
        "claims": [],
        "checks": [],
        "artifacts": [],
        "telemetry": {"exit_code": 0, "duration_s": 1.0, "executor_id": "e"},
    }


def _good_envelope():
    env = _envelope()
    env["claims"] = [{"id": "c1", "statement": "feito",
                      "evidence_refs": ["evidence/x.txt"]}]
    env["checks"] = [{"id": "k1", "criterion_id": "c1", "status": "pass",
                      "evidence_refs": ["evidence/x.txt"]}]
    return env


def _criteria():
    return [{"id": "c1", "required": True}]


def test_sem_opt_in_nao_roda():
    r = finalize_artifact(_envelope())
    assert r == {"pipeline": "eg3a", "ran": False}


def test_entrega_termina_em_revisao_humana_sempre():
    for verdict_env in (_good_envelope(), _envelope()):  # pass e inconclusive
        r = finalize_artifact(verdict_env, opt_in=True, acceptance_criteria=_criteria())
        assert r["ran"] is True
        assert r["delivery_status"] == FINAL_DELIVERY_STATUS == "awaiting_human_review"


def test_dimensoes_independentes_execucao_vs_validacao():
    # execução 'completed' mas validação falha: NÃO é contradição
    env = _envelope(status="completed")  # sem evidências → validation != pass
    r = finalize_artifact(env, opt_in=True, acceptance_criteria=_criteria())
    assert r["execution_status"] == "completed"
    assert r["validation_status"] in ("fail", "inconclusive")


def test_eg4_advisory_nunca_converte_em_pass():
    env = _envelope()  # inválido → fail/inconclusive
    r = finalize_artifact(env, opt_in=True, acceptance_criteria=_criteria(),
                          eg4_advisory="parecer: acredito que está bom")
    assert r["validation_status"] != "pass"
    assert r["eg4_advisory"].startswith("parecer")
    assert "EG4_ADVISORY_ATTACHED" in r["reason_codes"]
    assert r["delivery_status"] == "awaiting_human_review"


def test_advisory_ignorado_quando_pass():
    r = finalize_artifact(_good_envelope(), opt_in=True,
                          acceptance_criteria=_criteria(),
                          eg4_advisory="desnecessário")
    assert r["validation_status"] == "pass"
    assert "eg4_advisory" not in r  # advisory só por exceção


def test_tools_mcp_publicas_intactas():
    """Nenhuma tool nova declarada no pacote público."""
    from athena.mcp_server import contracts as mc
    src = Path(mc.__file__).read_text()
    public_tools = ("run_combo", "ask_provider", "get_execution",
                    "list_executions", "cancel_execution")
    for t in public_tools:
        assert t in src  # as cinco continuam definidas
    # pipeline não registra tool
    from athena.evidence_gate import pipeline_eg3a as p
    psrc = Path(p.__file__).read_text()
    assert "register_tool" not in psrc and "@tool" not in psrc
