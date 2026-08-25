"""ZEUS-GATE (Z-7): validação adversarial e de fronteiras do Zeus.

Cobre: zero seleção não autorizada, zero fallback silencioso, reason codes
explícitos, abstenção, determinismo, lifecycle e não-execução.
"""

from __future__ import annotations

import pytest

from athena.zeus import (
    AgentRecord,
    TaskRequest,
    ZeusRegistry,
    ZeusRouter,
    abstain,
    task_signature,
)


def _agent(agent_id="claude-code", persona="software.engineer.backend.v1",
           caps=("backend", "api", "tests"), runtime="frontier",
           lifecycle="approved", prohibited=()):
    return AgentRecord(
        agent_id=agent_id, persona_id=persona, registry_version="bootstrap",
        capabilities=frozenset(caps), runtime_class=runtime,
        lifecycle=lifecycle, prohibited_authorities=frozenset(prohibited),
    )


def _registry(*agents):
    reg = ZeusRegistry()
    reg.create_version(list(agents), action="create")
    return reg


def _task(**over):
    base = {
        "task_type": "backend",
        "primary_domain": "software.engineer.backend.v1",
        "risk_level": "low",
        "required_capabilities": ("backend", "api"),
        "explicit_agent_tag": None,
    }
    base.update(over)
    return TaskRequest(**base)


# ------------------------------------------------------- seleção básica


def test_selecao_basica_com_capacidade_e_reason_code():
    d = ZeusRouter(_registry(_agent())).route(_task())
    assert d.selected and d.agent_id == "claude-code"
    assert "CAPABILITY_MATCH" in d.reason_codes or "PERSONA_MATCH" in d.reason_codes
    assert d.runtime_class in ("local", "frontier")


def test_determinismo_mesma_entrada_mesmo_registro():
    reg = _registry(_agent(), _agent("cx2", caps=("backend", "api")))
    r1 = ZeusRouter(reg).route(_task())
    r2 = ZeusRouter(reg).route(_task())
    assert r1.to_dict() == r2.to_dict()
    assert r1.task_signature == task_signature(_task())


def test_registry_version_muda_muda_decisao_auditavel():
    reg = _registry(_agent())
    d1 = ZeusRouter(reg).route(_task())
    reg.suspend("claude-code")
    d2 = ZeusRouter(reg).route(_task())
    assert d1.registry_version != d2.registry_version
    assert not d2.selected  # único agente agora suspenso


# ---------------------------------------------------------- abstinências


def test_abstain_nenhum_agente_capaz():
    d = ZeusRouter(_registry(_agent(caps=("frontend",)))).route(
        _task(required_capabilities=("backend",)))
    assert not d.selected
    assert "ABSTAIN_NO_CAPABLE_AGENT" in d.reason_codes


def test_abstain_capabilidade_suspensa():
    reg = _registry(_agent())
    reg.suspend("claude-code")
    d = ZeusRouter(reg).route(_task())
    assert not d.selected
    assert "ABSTAIN_CAPABILITY_SUSPENDED" in d.reason_codes


def test_experimental_nunca_é_roteável():
    reg = _registry(_agent(lifecycle="experimental"))
    d = ZeusRouter(reg).route(_task())
    assert not d.selected
    assert "ABSTAIN_NO_CAPABLE_AGENT" in d.reason_codes


def test_retired_nunca_é_roteável():
    reg = _registry(_agent(lifecycle="retired"))
    d = ZeusRouter(reg).route(_task())
    assert not d.selected


# ------------------------------------------------- tag do usuário (sinal)


def test_tag_explicita_valida_vence_com_reason_code():
    reg = _registry(_agent("claude-code"), _agent("cx2", caps=("backend", "api")))
    d = ZeusRouter(reg).route(_task(explicit_agent_tag="cx2"))
    assert d.selected and d.agent_id == "cx2"
    assert "EXPLICIT_USER_TAG" in d.reason_codes


def test_tag_explicita_não_contorna_lifecycle():
    reg = _registry(_agent("claude-code", lifecycle="suspended"))
    d = ZeusRouter(reg).route(_task(explicit_agent_tag="claude-code"))
    assert not d.selected
    assert "ABSTAIN_CAPABILITY_SUSPENDED" in d.reason_codes


# ------------------------------------------------------------ autoridade


def test_autoridade_proibida_por_todos_bloqueia():
    reg = _registry(
        _agent(prohibited=("production_release",)),
        _agent("cx2", caps=("backend", "api"), prohibited=("production_release",)),
    )
    d = ZeusRouter(reg).route(
        _task(required_capabilities=("backend", "authority:production_release")))
    assert not d.selected
    assert "PROHIBITED_AUTHORITY_REQUESTED" in d.reason_codes


def test_autoridade_permitida_em_um_candidato_passa():
    reg = _registry(
        _agent(prohibited=("production_release",)),
        _agent("cx2", caps=("backend", "api")),  # sem proibição
    )
    d = ZeusRouter(reg).route(
        _task(required_capabilities=("backend", "authority:production_release")))
    assert d.selected and d.agent_id == "cx2"


# ------------------------------------------------------------- runtime


def test_risco_alto_marca_human_review_e_frontier():
    reg = _registry(_agent(runtime="local"))
    d = ZeusRouter(reg).route(_task(risk_level="high"))
    assert d.selected
    assert "HIGH_RISK_HUMAN_REVIEW" in d.reason_codes
    assert d.runtime_class == "frontier"


def test_risco_critico_vira_human_only():
    reg = _registry(_agent())
    d = ZeusRouter(reg).route(_task(risk_level="critical"))
    assert d.runtime_class == "human_only"


# --------------------------------------------------------------- Themis


def test_themis_sem_evidencia_é_ignorado_com_reason_code():
    reg = _registry(_agent("claude-code"), _agent("cx2", caps=("backend", "api")))
    d = ZeusRouter(reg, themis_scores={"cx2": 9.0}, themis_sufficient=False).route(_task())
    # sem evidência suficiente, ordem é alfabética estável -> claude-code
    assert d.agent_id == "claude-code"
    assert "ABSTAIN_THEMIS_INSUFFICIENT" in d.reason_codes


def test_themis_com_evidencia_desempata():
    reg = _registry(_agent("claude-code"), _agent("cx2", caps=("backend", "api")))
    d = ZeusRouter(reg, themis_scores={"cx2": 9.0}, themis_sufficient=True).route(_task())
    assert d.agent_id == "cx2"
    assert "THEMIS_EVIDENCE_SUFFICIENT" in d.reason_codes


# --------------------------------------------------------- não-execução


def test_zeus_nao_tem_superficie_de_execucao():
    """Zeus recomenda; não existe caminho de execução no módulo."""
    from athena import zeus

    public = [n for n in dir(zeus) if not n.startswith("_")]
    forbidden = ("run", "execute", "spawn", "subprocess", "dispatch")
    for name in public:
        for bad in forbidden:
            assert not name.lower().startswith(bad), f"símbolo suspeito: {name}"
    # nenhuma fábrica retorna processo/pipe
    import inspect
    for name in ("ZeusRouter", "ZeusRegistry"):
        members = inspect.getmembers(getattr(zeus, name))
        for mname, mobj in members:
            if callable(mobj) and any(b in mname.lower() for b in ("exec", "spawn", "popen")):
                raise AssertionError(f"método de execução proibido: {name}.{mname}")


def test_abstencao_fabrica_so_aceita_codigos_de_abstencao():
    with pytest.raises(ValueError):
        abstain("sig", "v1", ("EXPLICIT_USER_TAG",), 0.9)


def test_decisao_abstencao_nao_pode_carregar_agente():
    from athena.zeus.contracts import ZeusDecision

    with pytest.raises(ValueError):
        ZeusDecision(
            task_signature="s", registry_version="v", selected=False,
            agent_id="claude-code", persona_id=None, model_hint=None,
            runtime_class=None, reason_codes=("ABSTAIN_LOW_CONFIDENCE",),
            confidence=0.2,
        )


def test_reason_code_desconhecido_rejeitado():
    from athena.zeus.contracts import ZeusDecision

    with pytest.raises(ValueError):
        ZeusDecision(
            task_signature="s", registry_version="v", selected=True,
            agent_id="a", persona_id="p", model_hint=None,
            runtime_class="local", reason_codes=("MOTIVO_INVENTADO",),
            confidence=0.9,
        )
