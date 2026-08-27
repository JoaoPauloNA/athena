"""Z-REALIGN: testes de fronteira Zeus/Nike pós-realign.

Invariante central: Zeus recomenda; Nike resolve runtime/provider;
nenhum dos dois executa ou redefine política.
"""

from __future__ import annotations

from athena.zeus import AgentRecord, TaskRequest, ZeusRegistry
from athena.zeus.nike import NikeSelector
from athena.zeus.realign import zeus_never_executes_check


def _reg(*caps_extra: str) -> ZeusRegistry:
    reg = ZeusRegistry()
    recs = [
        AgentRecord("agent-a", "p.backend", "b",
                    frozenset({"backend", *caps_extra}), "local",
                    lifecycle="approved"),
        AgentRecord("agent-b", "p.backend", "b",
                    frozenset({"backend", *caps_extra}), "frontier",
                    lifecycle="approved"),
    ]
    reg.create_version(recs, action="create")
    return reg


def _task(risk="low"):
    return TaskRequest(task_type="backend", primary_domain="p.backend",
                       risk_level=risk, required_capabilities=("backend",))


def test_zeus_nunca_preenche_model_hint():
    """Zeus recomenda especialista; seleção concreta de modelo não é dele."""
    d = NikeSelector(_reg()).route(_task())
    assert d.selected and d.model_hint is None


def test_nike_resolve_runtime_via_requisito_nao_por_modelo():
    """Nike com providers.json determina runtime disponível; sem config,
    mantém o runtime_class do agente (não inventa modelo)."""
    scores = {"agent-a": {"valid": True, "final_score": 8.0}}
    sel = NikeSelector(_reg(), scores)
    d = sel.route(_task())
    assert d.runtime_class == "local"  # veio do AgentRecord, não de resolução própria


def test_abstain_no_capable_agent_preservado():
    reg = ZeusRegistry()
    reg.create_version([
        AgentRecord("agent-c", "p.frontend", "f",
                    frozenset({"frontend"}), "local", lifecycle="approved"),
    ], action="create")
    d = NikeSelector(reg).route(_task())
    assert not d.selected
    assert "ABSTAIN_NO_CAPABLE_AGENT" in d.reason_codes


def test_nike_nao_redefine_risco_alto_para_humano():
    """Risco alto altera runtime do agente escolhido, nunca troca o escopo."""
    scores = {"agent-a": {"valid": True, "final_score": 9.9}}
    d = NikeSelector(_reg(), scores).route(_task(risk="high"))
    assert d.selected and d.agent_id == "agent-a"
    assert "HIGH_RISK_HUMAN_REVIEW" in d.reason_codes
    assert d.model_hint is None  # mesmo com risco alto, Zeus/Nike não escolhem modelo


def test_superficie_sem_execucao():
    assert zeus_never_executes_check() is True


def test_determinismo_intacto_pos_realign():
    reg = _reg()
    s1 = NikeSelector(reg).route(_task()).to_dict()
    s2 = NikeSelector(_reg()).route(_task()).to_dict()
    # registry_version pode diferir entre instâncias? Não: mesmos dados, mesma versão derivada
    s1.pop("registry_version"); s2.pop("registry_version")
    assert s1 == s2
