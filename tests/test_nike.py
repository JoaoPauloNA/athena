"""Testes do Nike: Themis válido desempata; inválido é ignorado com código."""

from __future__ import annotations

from athena.zeus import AgentRecord, TaskRequest, ZeusRegistry
from athena.zeus.nike import NikeSelector


def _reg():
    reg = ZeusRegistry()
    reg.create_version([
        AgentRecord("agent-a", "p.backend", "b",
                    frozenset({"backend"}), "frontier", lifecycle="approved"),
        AgentRecord("agent-b", "p.backend", "b",
                    frozenset({"backend"}), "frontier", lifecycle="approved"),
    ], action="create")
    return reg


def _task():
    return TaskRequest(task_type="backend", primary_domain="p.backend",
                       risk_level="low", required_capabilities=("backend",))


def test_themis_valido_desempata():
    scores = {
        "agent-a": {"valid": True, "final_score": 4.0},
        "agent-b": {"valid": True, "final_score": 8.5},
    }
    d = NikeSelector(_reg(), scores).route(_task())
    assert d.selected and d.agent_id == "agent-b"
    assert "THEMIS_EVIDENCE_SUFFICIENT" in d.reason_codes


def test_themis_invalido_ignorado_ordem_estavel():
    scores = {
        "agent-a": {"valid": False, "final_score": 9.9},  # N insuficiente
        "agent-b": {"valid": False, "final_score": 2.0},
    }
    r = NikeSelector(_reg(), scores).route_with_report(_task())
    assert not r["themis_consulted"]
    assert "ABSTAIN_THEMIS_INSUFFICIENT" in r["decision"]["reason_codes"]
    # ordem alfabética estável
    assert r["decision"]["agent_id"] == "agent-a"


def test_report_expoe_chaves_validas():
    scores = {"agent-a": {"valid": True, "final_score": 7.0}}
    r = NikeSelector(_reg(), scores).route_with_report(_task())
    assert r["themis_consulted"]
    assert r["themis_valid_keys"] == ["agent-a"]
