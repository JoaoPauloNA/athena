"""Z-REALIGN v4 (GATE 2) — prova semântica da separação Zeus/Nike.

Sem varredura ingênua de código: exercita comportamento observável.
Casos obrigatórios do gate + preservações canônicas.
"""

from __future__ import annotations

import json

import pytest

from athena.config_loader import provider_eligible
from athena.zeus import AgentRecord, TaskRequest, ZeusRegistry
from athena.zeus.contracts import REASON_CODES
from athena.zeus.realign import (
    REASON_NO_PROVIDER,
    REASON_RESOLVED,
    NikeRuntimeSelector,
    ZeusEligibilityRouter,
)


def _reg() -> ZeusRegistry:
    reg = ZeusRegistry()
    reg.create_version([
        AgentRecord("agent-local", "p.backend", "b",
                    frozenset({"backend"}), "local", lifecycle="approved"),
        AgentRecord("agent-either", "p.backend", "b",
                    frozenset({"backend"}), "either", lifecycle="approved"),
        AgentRecord("agent-frontier", "p.backend", "b",
                    frozenset({"backend"}), "frontier", lifecycle="approved"),
    ], action="create")
    return reg


def _task(risk="low"):
    return TaskRequest(task_type="backend", primary_domain="p.backend",
                       risk_level=risk, required_capabilities=("backend",))


def _healthy(pid):
    return {"provider_id": pid, "discovered": True, "healthy": True}


# ------------------------------------------------- Zeus: só elegibilidade

@pytest.mark.parametrize("risk", ["low", "high", "critical"])
def test_eligibility_nunca_carrega_provider_modelo_runtime(risk):
    e = ZeusEligibilityRouter(_reg()).eligibility(_task(risk))
    d = e.to_dict()
    assert "provider_id" not in d and "model_id" not in d
    assert "mode" not in d
    assert e.eligible_specialist_id in {"agent-local", "agent-either",
                                        "agent-frontier"}
    assert e.risk_level == risk  # risco espelhado, nunca alterado


def test_runtime_classes_do_requisito():
    reg = _reg()
    for aid, want in (("agent-local", ("local",)),
                      ("agent-either", ("local", "frontier")),
                      ("agent-frontier", ("frontier",))):
        req = TaskRequest("backend", "p.backend", "low", ("backend",),
                          explicit_agent_tag=aid)
        e = ZeusEligibilityRouter(reg).eligibility(req)
        assert e.required_runtime_classes == want, aid


def test_abstain_no_capable_agent_preservado():
    reg = ZeusRegistry()
    reg.create_version([AgentRecord("agent-f", "p.frontend", "f",
                                    frozenset({"frontend"}), "local",
                                    lifecycle="approved")], action="create")
    e = ZeusEligibilityRouter(reg).eligibility(_task())
    assert e.eligible_specialist_id is None
    assert "ABSTAIN_NO_CAPABLE_AGENT" in e.reason_codes


def test_prohibited_authority_razao_exata():
    reg = ZeusRegistry()
    reg.create_version([AgentRecord("agent-x", "p.backend", "b",
                                    frozenset({"backend", "write_file"}),
                                    "local", lifecycle="approved",
                                    prohibited_authorities=frozenset({"write_file"}))],
                       action="create")
    req = TaskRequest("backend", "p.backend", "low",
                      ("backend", "authority:write_file"))
    e = ZeusEligibilityRouter(reg).eligibility(req)
    assert e.eligible_specialist_id is None
    # razão EXATA (não colapsada em ABSTAIN_NO_CAPABLE_AGENT)
    assert e.reason_codes == ("PROHIBITED_AUTHORITY_REQUESTED",)


def test_explicit_tag_seleciona_especialista():
    reg = _reg()
    req = TaskRequest("backend", "p.backend", "low", ("backend",),
                      explicit_agent_tag="agent-frontier")
    e = ZeusEligibilityRouter(reg).eligibility(req)
    assert e.eligible_specialist_id == "agent-frontier"


def test_determinismo_byte_stable():
    outs = [json.dumps(ZeusEligibilityRouter(_reg())
                       .eligibility(_task()).to_dict(), sort_keys=True)
            for _ in range(2)]
    assert outs[0] == outs[1]


# ------------------------------------------------- Nike: resolução concreta

def _cache(tmp_path, entries):
    cache = tmp_path / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "inventory.json").write_text(json.dumps({"entries": entries}))
    return cache


LOCAL_SPEC = {"mode": "local", "runtime_class": "local",
              "enabled": True, "approved": True,
              "base_url": "http://127.0.0.1:11434"}


@pytest.mark.parametrize("mode,rc,tag", [
    ("local", "local", "agent-local"),
    ("api", "frontier", "agent-frontier"),
    ("agent_cli", "frontier", "agent-frontier"),
    ("remote", "frontier", "agent-frontier"),
])
def test_combinacoes_validas_mode_runtime(tmp_path, mode, rc, tag):
    """modos válidos × runtime_class — validação aceita e classe casa."""
    spec = {"mode": mode, "runtime_class": rc,
            "enabled": True, "approved": True}
    if mode == "agent_cli":
        spec["command"] = "claude"
    providers = {f"p-{mode.replace('_', '')}": spec}
    # validação de config aceita (mode válido, classe separada)
    from athena.config_loader import validate_providers
    validate_providers(providers)
    # requisito do especialista contém a classe
    req = TaskRequest("backend", "p.backend", "low", ("backend",),
                      explicit_agent_tag=tag)
    e = ZeusEligibilityRouter(_reg()).eligibility(req)
    assert rc in e.required_runtime_classes
    # sem healthy → abstenção fail-closed correta
    nd = NikeRuntimeSelector(ZeusEligibilityRouter(_reg()), providers).resolve(req)
    assert nd.abstained and REASON_NO_PROVIDER in nd.reason_codes


def test_local_local_resolve_com_healthy(tmp_path):
    cache = _cache(tmp_path, [_healthy("p-local")])
    providers = {"p-local": LOCAL_SPEC}
    nd = NikeRuntimeSelector(ZeusEligibilityRouter(_reg()), providers,
                             cache).resolve(
        TaskRequest("backend", "p.backend", "low", ("backend",),
                    explicit_agent_tag="agent-local"))
    assert not nd.abstained
    assert nd.provider_id == "p-local"
    assert nd.mode == "local" and nd.runtime_class == "local"
    assert REASON_RESOLVED in nd.reason_codes


def test_api_frontier_healthy_resolve(tmp_path):
    cache = _cache(tmp_path, [_healthy("cliproxy")])
    providers = {"cliproxy": {"mode": "api", "runtime_class": "frontier",
                              "enabled": True, "approved": True,
                              "base_url": "http://127.0.0.1:8317/v1",
                              "default_model": "claude-haiku-4-5"}}
    nd = NikeRuntimeSelector(ZeusEligibilityRouter(_reg()), providers,
                             cache).resolve(
        TaskRequest("backend", "p.backend", "low", ("backend",),
                    explicit_agent_tag="agent-frontier"))
    assert not nd.abstained
    assert nd.mode == "api" and nd.runtime_class == "frontier"
    assert nd.model_id == "claude-haiku-4-5"


def test_unhealthy_rejeitado(tmp_path):
    cache = _cache(tmp_path, [{"provider_id": "p-local", "healthy": False}])
    nd = NikeRuntimeSelector(ZeusEligibilityRouter(_reg()),
                             {"p-local": LOCAL_SPEC}, cache).resolve(
        TaskRequest("backend", "p.backend", "low", ("backend",),
                    explicit_agent_tag="agent-local"))
    assert nd.abstained and REASON_NO_PROVIDER in nd.reason_codes


def test_healthy_passa_mesmo_sem_discovered(tmp_path):
    """GATE 1: discovered ausente NÃO bloqueia quando healthy=true."""
    cache = _cache(tmp_path, [{"provider_id": "p-local", "healthy": True}])
    nd = NikeRuntimeSelector(ZeusEligibilityRouter(_reg()),
                             {"p-local": LOCAL_SPEC}, cache).resolve(
        TaskRequest("backend", "p.backend", "low", ("backend",),
                    explicit_agent_tag="agent-local"))
    assert not nd.abstained


def test_discovered_apenas_falha(tmp_path):
    cache = _cache(tmp_path, [{"provider_id": "p-local",
                               "discovered": True, "healthy": False}])
    nd = NikeRuntimeSelector(ZeusEligibilityRouter(_reg()),
                             {"p-local": LOCAL_SPEC}, cache).resolve(
        TaskRequest("backend", "p.backend", "low", ("backend",),
                    explicit_agent_tag="agent-local"))
    assert nd.abstained


def test_aegis_denial_vence(tmp_path):
    cache = _cache(tmp_path, [_healthy("p-local")])
    nd = NikeRuntimeSelector(ZeusEligibilityRouter(_reg()),
                             {"p-local": LOCAL_SPEC}, cache).resolve(
        TaskRequest("backend", "p.backend", "low", ("backend",),
                    explicit_agent_tag="agent-local"),
        aegis_allows=False)
    assert nd.abstained and nd.aegis_allowed is False


def test_selecao_deterministica_de_provider(tmp_path):
    cache = _cache(tmp_path, [_healthy("b-prov"), _healthy("a-prov")])
    providers = {
        "b-prov": dict(LOCAL_SPEC),
        "a-prov": dict(LOCAL_SPEC),
    }
    d1 = NikeRuntimeSelector(ZeusEligibilityRouter(_reg()), providers,
                             cache).resolve(
        TaskRequest("backend", "p.backend", "low", ("backend",),
                    explicit_agent_tag="agent-local")).to_dict()
    d2 = NikeRuntimeSelector(ZeusEligibilityRouter(_reg()), providers,
                             cache).resolve(
        TaskRequest("backend", "p.backend", "low", ("backend",),
                    explicit_agent_tag="agent-local")).to_dict()
    assert d1 == d2 and d1["provider_id"] == "a-prov"  # ordem alfabética


def test_config_invalida_falha_fechada():
    # mode=frontier aqui é proposital: fixture INVÁLIDA que deve ser rejeitada
    with pytest.raises(ValueError):
        NikeRuntimeSelector(ZeusEligibilityRouter(_reg()),
                            {"x": {"mode": "frontier", "runtime_class": "frontier",
                                   "enabled": True, "approved": True}})


def test_providers_sem_config_abste():
    nd = NikeRuntimeSelector(ZeusEligibilityRouter(_reg()), None).resolve(_task())
    assert nd.abstained and REASON_NO_PROVIDER in nd.reason_codes


def test_reason_codes_versionados():
    assert REASON_RESOLVED in REASON_CODES
    assert REASON_NO_PROVIDER in REASON_CODES


def test_routing_sem_side_effects(tmp_path):
    """resolve() sem cache-dir e sem providers: nenhum FS write/rede."""
    before = sorted(p.name for p in tmp_path.iterdir())
    nd = NikeRuntimeSelector(ZeusEligibilityRouter(_reg()), None).resolve(_task())
    assert nd.abstained
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_provider_eligible_formula_canonica():
    spec = LOCAL_SPEC
    # healthy true mesmo sem discovered → elegível
    ok, _ = provider_eligible(spec, {"healthy": True})
    assert ok
    # discovered true mas healthy false → rejeitado
    ok, reason = provider_eligible(spec, {"discovered": True, "healthy": False})
    assert not ok and reason == "PROVIDER_UNHEALTHY"
    # sem inventário → fail closed unhealthy
    ok, reason = provider_eligible(spec, None)
    assert not ok and reason == "PROVIDER_UNHEALTHY"
