"""Testes determinísticos da política de verificação reforçada.

Cobre a função pura (`evaluate_reinforced_verification`), a emissão do evento
no Flight Recorder e a integração com o router de cadeias — incluindo a
garantia de que a cadeia normal existente continua funcionando sem exigir
verificação reforçada.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from athena import router, workspace_lease
from athena.bridge import RunResult
from athena.combos import Combo, ComboStep, FailoverPolicy
from athena.reinforced_verification import (
    REASON_COMPLEX_NON_STANDARD,
    REASON_FALLBACK_OUTSIDE_CHAIN,
    REASON_LOCAL_MODEL,
    REASON_OUTSIDE_RECOMMENDED_LEVEL,
    REINFORCED_VERIFICATION_EVENT,
    ReinforcedVerificationContext,
    emit_reinforced_verification_event,
    evaluate_reinforced_verification,
)

# Prompts determinísticos para athena.recommend.estimate_complexity:
# "arquitetura" está em _COMPLEX_HINTS; "renomear" está em _SIMPLE_HINTS.
COMPLEX_PROMPT = "revisar a arquitetura do modulo de pagamentos"
SIMPLE_PROMPT = "renomear a variavel de contagem"


@pytest.fixture(autouse=True)
def _clean_workspace_lease_registry():
    """O registry de leases é estado global de processo: isola cada teste."""
    workspace_lease._reset_for_tests()
    yield
    workspace_lease._reset_for_tests()


def _read_events(logs_dir: Path) -> list[dict]:
    events: list[dict] = []
    for path in sorted(logs_dir.glob("**/events.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    return events


# --------------------------------------------------------------------------
# Função pura
# --------------------------------------------------------------------------


def test_local_model_activates():
    """Regra 1: modelo de IA local ativa verificação reforçada."""
    decision = evaluate_reinforced_verification(
        ReinforcedVerificationContext(
            used_provider_id="ollama",
            used_model="llama3",
            original_chain=("ollama",),
            standard_provider_ids=("ollama",),
        )
    )
    assert decision.requires_reinforced_verification is True
    assert REASON_LOCAL_MODEL in decision.reasons


def test_local_model_by_service_profile_activates():
    """Perfil de serviço local_model também caracteriza modelo local."""
    decision = evaluate_reinforced_verification(
        ReinforcedVerificationContext(
            used_provider_id="provider-x",
            service_profile_id="local_model",
            original_chain=("provider-x",),
            standard_provider_ids=("provider-x",),
        )
    )
    assert decision.requires_reinforced_verification is True
    assert REASON_LOCAL_MODEL in decision.reasons


def test_complex_task_with_standard_agent_does_not_activate_alone():
    """Regra chave: tarefa complexa sozinha NÃO ativa.

    Executada pela cadeia prevista e no nível recomendado, uma tarefa
    complexa não exige verificação reforçada.
    """
    decision = evaluate_reinforced_verification(
        ReinforcedVerificationContext(
            used_provider_id="claude",
            used_model="opus",
            used_weight="heavy",
            task_complexity="complex",
            standard_provider_ids=("claude",),
            original_chain=("claude", "codex"),
            is_fallback=False,
        )
    )
    assert decision.requires_reinforced_verification is False
    assert decision.reasons == []


def test_complex_task_with_non_standard_agent_activates():
    """Regra 2: tarefa complexa + agente fora do padrão ativa.

    O agente continua na cadeia original (fallback previsto), então a regra 4
    não dispara — isolando a regra 2.
    """
    decision = evaluate_reinforced_verification(
        ReinforcedVerificationContext(
            used_provider_id="codex",
            used_model="gpt-5.5",
            used_weight="heavy",
            task_complexity="complex",
            standard_provider_ids=("claude",),
            original_chain=("claude", "codex"),
            is_fallback=True,
        )
    )
    assert decision.requires_reinforced_verification is True
    assert REASON_COMPLEX_NON_STANDARD in decision.reasons
    assert REASON_FALLBACK_OUTSIDE_CHAIN not in decision.reasons


def test_agent_outside_recommended_level_activates():
    """Regra 3: nível do agente diferente do recomendado ativa."""
    decision = evaluate_reinforced_verification(
        ReinforcedVerificationContext(
            used_provider_id="claude",
            used_model="haiku",
            used_weight="light",
            task_complexity="complex",
            standard_provider_ids=("claude",),
            original_chain=("claude",),
        )
    )
    assert decision.requires_reinforced_verification is True
    assert REASON_OUTSIDE_RECOMMENDED_LEVEL in decision.reasons
    assert (
        decision.details[REASON_OUTSIDE_RECOMMENDED_LEVEL]["direction"]
        == "below_recommended"
    )


def test_agent_above_recommended_level_activates():
    """Desvio de nível para cima também é desvio do plano da tarefa."""
    decision = evaluate_reinforced_verification(
        ReinforcedVerificationContext(
            used_provider_id="claude",
            used_model="opus",
            used_weight="heavy",
            task_complexity="simple",
            standard_provider_ids=("claude",),
            original_chain=("claude",),
        )
    )
    assert decision.requires_reinforced_verification is True
    assert (
        decision.details[REASON_OUTSIDE_RECOMMENDED_LEVEL]["direction"]
        == "above_recommended"
    )


def test_fallback_outside_original_chain_activates():
    """Regra 4: agente não previsto na cadeia original ativa."""
    decision = evaluate_reinforced_verification(
        ReinforcedVerificationContext(
            used_provider_id="intruso",
            used_model="modelo-x",
            used_weight="medium",
            task_complexity="medium",
            standard_provider_ids=("claude",),
            original_chain=("claude", "codex"),
            is_fallback=True,
        )
    )
    assert decision.requires_reinforced_verification is True
    assert REASON_FALLBACK_OUTSIDE_CHAIN in decision.reasons


def test_multiple_reasons_are_preserved():
    """Todas as razões aplicáveis são preservadas, sem short-circuit."""
    decision = evaluate_reinforced_verification(
        ReinforcedVerificationContext(
            used_provider_id="ollama",
            used_model="llama3",
            used_weight="light",
            task_complexity="complex",
            standard_provider_ids=("claude",),
            original_chain=("claude", "codex"),
            is_fallback=True,
        )
    )
    assert decision.requires_reinforced_verification is True
    assert set(decision.reasons) == {
        REASON_LOCAL_MODEL,
        REASON_COMPLEX_NON_STANDARD,
        REASON_OUTSIDE_RECOMMENDED_LEVEL,
        REASON_FALLBACK_OUTSIDE_CHAIN,
    }
    assert len(decision.reasons) == 4


def test_unknown_context_does_not_activate():
    """Contexto insuficiente nunca ativa: política exige evidência positiva."""
    decision = evaluate_reinforced_verification(
        ReinforcedVerificationContext(used_provider_id="claude")
    )
    assert decision.requires_reinforced_verification is False
    assert decision.reasons == []


def test_decision_to_dict_roundtrip():
    """to_dict expõe o contrato consumido pelo RunResult."""
    decision = evaluate_reinforced_verification(
        ReinforcedVerificationContext(used_provider_id="ollama")
    )
    payload = decision.to_dict()
    assert payload["requires_reinforced_verification"] is True
    assert payload["reasons"] == [REASON_LOCAL_MODEL]
    assert REASON_LOCAL_MODEL in payload["details"]


# --------------------------------------------------------------------------
# Flight Recorder
# --------------------------------------------------------------------------


def test_flight_recorder_event_is_produced(tmp_path):
    """Evento reinforced_verification_required é gravado quando exigido."""
    decision = evaluate_reinforced_verification(
        ReinforcedVerificationContext(
            used_provider_id="ollama",
            used_model="llama3",
            original_chain=("claude",),
        )
    )
    emit_reinforced_verification_event(
        decision,
        execution_id="exec-1",
        combo_id="combo-1",
        provider_id="ollama",
        attempted_chain=["claude", "ollama"],
        original_chain=["claude"],
        logs_dir=tmp_path,
    )
    events = [
        e for e in _read_events(tmp_path)
        if e["event_type"] == REINFORCED_VERIFICATION_EVENT
    ]
    assert len(events) == 1
    event = events[0]
    assert event["requires_reinforced_verification"] is True
    assert event["execution_id"] == "exec-1"
    assert event["combo_id"] == "combo-1"
    assert set(event["reasons"]) == {REASON_LOCAL_MODEL, REASON_FALLBACK_OUTSIDE_CHAIN}


def test_flight_recorder_event_not_emitted_when_not_required(tmp_path):
    """Sem exigência não há evento: o log não vira ruído."""
    decision = evaluate_reinforced_verification(
        ReinforcedVerificationContext(used_provider_id="claude")
    )
    emit_reinforced_verification_event(
        decision,
        execution_id="exec-2",
        combo_id="combo-2",
        provider_id="claude",
        logs_dir=tmp_path,
    )
    assert _read_events(tmp_path) == []


# --------------------------------------------------------------------------
# Integração com o router
# --------------------------------------------------------------------------


def _combo(chain: list[ComboStep], retries: int = 1) -> Combo:
    return Combo(
        id="t",
        name="t",
        chain=chain,
        failover_policy=FailoverPolicy(max_retries_per_provider=retries),
    )


def _ok(provider: str) -> RunResult:
    return RunResult(provider=provider, command=[], output="ok", exit_code=0)


def _fail(provider: str) -> RunResult:
    return RunResult(
        provider=provider, command=[], output="", exit_code=1, error="boom"
    )


def test_router_normal_chain_still_works(monkeypatch, tmp_path):
    """Cadeia normal existente continua funcionando e não exige reforço."""
    monkeypatch.setenv("ATHENA_LOGS_DIR", str(tmp_path))
    monkeypatch.setattr(
        router,
        "get_combo",
        lambda cid: _combo([ComboStep(provider_id="p1"), ComboStep(provider_id="p2")]),
    )
    monkeypatch.setattr(router, "ask_provider", lambda p, pr, **kw: _ok(p))

    result = router.run_combo("t", SIMPLE_PROMPT)

    assert result.exit_code == 0
    assert result.output == "ok"
    assert result.reinforced_verification is not None
    assert result.reinforced_verification["requires_reinforced_verification"] is False
    assert result.reinforced_verification["reasons"] == []
    assert _read_events(tmp_path) == []


def test_router_failover_within_chain_does_not_require_reinforcement(
    monkeypatch, tmp_path
):
    """Fallback previsto na cadeia, tarefa simples: não exige reforço."""
    monkeypatch.setenv("ATHENA_LOGS_DIR", str(tmp_path))
    monkeypatch.setattr(
        router,
        "get_combo",
        lambda cid: _combo([ComboStep(provider_id="p1"), ComboStep(provider_id="p2")]),
    )

    def fake_ask(provider_id, prompt, **kw):
        return _fail(provider_id) if provider_id == "p1" else _ok(provider_id)

    monkeypatch.setattr(router, "ask_provider", fake_ask)

    result = router.run_combo("t", SIMPLE_PROMPT)

    assert result.exit_code == 0
    assert result.reinforced_verification["requires_reinforced_verification"] is False


def test_router_local_model_requires_reinforcement_and_emits_event(
    monkeypatch, tmp_path
):
    """Integração ponta a ponta: modelo local sinaliza e registra evento."""
    monkeypatch.setenv("ATHENA_LOGS_DIR", str(tmp_path))
    monkeypatch.setattr(
        router,
        "get_combo",
        lambda cid: _combo([ComboStep(provider_id="ollama", model="llama3")]),
    )
    monkeypatch.setattr(router, "ask_provider", lambda p, pr, **kw: _ok(p))

    result = router.run_combo("t", SIMPLE_PROMPT)

    assert result.exit_code == 0
    assert result.output == "ok"  # resultado original preservado
    assert result.reinforced_verification["requires_reinforced_verification"] is True
    assert REASON_LOCAL_MODEL in result.reinforced_verification["reasons"]
    assert any("Verificação reforçada exigida" in w for w in result.warnings)

    events = [
        e for e in _read_events(tmp_path)
        if e["event_type"] == REINFORCED_VERIFICATION_EVENT
    ]
    assert len(events) == 1
    assert events[0]["provider_id"] == "ollama"
    assert events[0]["combo_id"] == "t"


def test_router_complex_task_with_fallback_agent_requires_reinforcement(
    monkeypatch, tmp_path
):
    """Tarefa complexa concluída por agente fora do padrão exige reforço."""
    monkeypatch.setenv("ATHENA_LOGS_DIR", str(tmp_path))
    monkeypatch.setattr(
        router,
        "get_combo",
        lambda cid: _combo([ComboStep(provider_id="p1"), ComboStep(provider_id="p2")]),
    )

    def fake_ask(provider_id, prompt, **kw):
        return _fail(provider_id) if provider_id == "p1" else _ok(provider_id)

    monkeypatch.setattr(router, "ask_provider", fake_ask)

    result = router.run_combo("t", COMPLEX_PROMPT)

    assert result.exit_code == 0
    assert result.reinforced_verification["requires_reinforced_verification"] is True
    assert REASON_COMPLEX_NON_STANDARD in result.reinforced_verification["reasons"]


def test_router_complex_task_on_primary_agent_does_not_require_reinforcement(
    monkeypatch, tmp_path
):
    """Contraprova: mesma tarefa complexa pelo agente padrão não exige reforço."""
    monkeypatch.setenv("ATHENA_LOGS_DIR", str(tmp_path))
    monkeypatch.setattr(
        router,
        "get_combo",
        lambda cid: _combo([ComboStep(provider_id="p1"), ComboStep(provider_id="p2")]),
    )
    monkeypatch.setattr(router, "ask_provider", lambda p, pr, **kw: _ok(p))

    result = router.run_combo("t", COMPLEX_PROMPT)

    assert result.exit_code == 0
    assert result.reinforced_verification["requires_reinforced_verification"] is False
    assert _read_events(tmp_path) == []


def test_router_result_to_dict_exposes_decision(monkeypatch, tmp_path):
    """A decisão viaja no contrato serializado do resultado."""
    monkeypatch.setenv("ATHENA_LOGS_DIR", str(tmp_path))
    monkeypatch.setattr(
        router,
        "get_combo",
        lambda cid: _combo([ComboStep(provider_id="ollama", model="llama3")]),
    )
    monkeypatch.setattr(router, "ask_provider", lambda p, pr, **kw: _ok(p))

    payload = router.run_combo("t", SIMPLE_PROMPT).to_dict()

    assert payload["reinforced_verification"]["requires_reinforced_verification"] is True
