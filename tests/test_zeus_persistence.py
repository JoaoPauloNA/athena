"""CFG-2: testes de persistência do registro do Zeus.

Invariantes:
- registro sobrevive a reinício (save → nova instância → load);
- hash divergente recusa a carga inteira;
- gravação é idempotente por conteúdo;
- determinismo preservado após round-trip.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from athena.zeus import AgentRecord, TaskRequest, ZeusRegistry
from athena.zeus.nike import NikeSelector
from athena.zeus.persistence import ConfigLoadError, load_registry, save_registry


def _reg() -> ZeusRegistry:
    reg = ZeusRegistry()
    recs = [
        AgentRecord("agent-a", "p.backend", "b", frozenset({"backend"}),
                    "local", lifecycle="approved"),
        AgentRecord("agent-b", "p.backend", "b", frozenset({"backend"}),
                    "frontier", lifecycle="approved"),
    ]
    v1 = reg.create_version(recs, action="create")
    # segunda versão suspendendo agent-b
    base = list(reg.snapshot(v1).values())
    mutated = [
        AgentRecord(a.agent_id, a.persona_id, a.registry_version, a.capabilities,
                    a.runtime_class,
                    lifecycle="suspended" if a.agent_id == "agent-b" else a.lifecycle)
        for a in base
    ]
    reg.create_version(mutated, action="suspend", agent_id="agent-b")
    return reg


def _task():
    return TaskRequest(task_type="backend", primary_domain="p.backend",
                       risk_level="low", required_capabilities=("backend",))


def test_roundtrip_sobrevive_reinicio(tmp_path: Path):
    reg = _reg()
    save_registry(reg, tmp_path)
    reg2 = load_registry(tmp_path)
    assert reg2.current_version == reg.current_version
    snap = reg2.snapshot()
    assert set(snap) == {"agent-a", "agent-b"}
    assert snap["agent-a"].lifecycle == "approved"
    assert snap["agent-b"].lifecycle == "suspended"
    # decisões idênticas após round-trip
    d1 = NikeSelector(reg).route(_task()).to_dict()
    d2 = NikeSelector(reg2).route(_task()).to_dict()
    d1.pop("registry_version"); d2.pop("registry_version")
    assert d1 == d2


def test_hash_divergente_recusa_tudo(tmp_path: Path):
    reg = _reg()
    save_registry(reg, tmp_path)
    victim = next(p for p in (tmp_path / "zeus-registry").glob("*.json")
                  if not str(p).endswith("current.json"))
    payload = json.loads(victim.read_text())  # lista de agentes
    payload[0]["capabilities"] = ["hacked"]
    victim.write_text(json.dumps(payload))
    with pytest.raises(ConfigLoadError, match="hash divergente"):
        load_registry(tmp_path)


def test_gravacao_idempotente(tmp_path: Path):
    reg = _reg()
    save_registry(reg, tmp_path)
    first = sorted((p.name, p.read_bytes()) for p in (tmp_path / "zeus-registry").iterdir())
    save_registry(reg, tmp_path)
    second = sorted((p.name, p.read_bytes()) for p in (tmp_path / "zeus-registry").iterdir())
    assert first == second


def test_registro_vazio_nao_exporta(tmp_path: Path):
    with pytest.raises(RuntimeError, match="registro vazio"):
        save_registry(ZeusRegistry(), tmp_path)
