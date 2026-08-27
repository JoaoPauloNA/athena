"""Registro versionado de agentes/personas (Z-2).

O registro é um documento JSON imutável por versão: criar, suspender ou
aposentar um agente produz uma NOVA versão — nunca mutação silenciosa.
Seleção determinística exige (entrada + registry_version) fixos.
"""

from __future__ import annotations

from typing import Any

from .contracts import REGISTRY_SCHEMA_VERSION, AgentRecord


class ZeusRegistry:
    """Registro em memória com histórico de versões e transições auditáveis."""

    def __init__(self) -> None:
        self._versions: dict[str, dict[str, AgentRecord]] = {}
        self._current: str | None = None
        self._transitions: list[dict[str, Any]] = []  # auditoria de lifecycle

    # ------------------------------------------------------------ leitura

    @property
    def current_version(self) -> str:
        if self._current is None:
            raise RuntimeError("registro vazio: nenhuma versão criada")
        return self._current

    def snapshot(self, version: str | None = None) -> dict[str, AgentRecord]:
        """Cópia defensiva da versão pedida (default: atual)."""
        v = version or self.current_version
        try:
            return dict(self._versions[v])
        except KeyError as exc:
            raise KeyError(f"versão do registro inexistente: {v}") from exc

    def history(self) -> list[str]:
        """Versões na ordem de criação."""
        return list(self._versions.keys())

    def transitions(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._transitions)

    # ------------------------------------------------------------ escrita

    def _new_version(self) -> str:
        n = len(self._versions) + 1
        v = f"{REGISTRY_SCHEMA_VERSION.split('.')[2]}.0.{n}"  # "v1.0.N"
        return f"zeus.registry.{v}"

    def create_version(
        self,
        agents: list[AgentRecord],
        *,
        action: str,
        agent_id: str | None = None,
    ) -> str:
        """Publicar uma nova versão a partir da lista completa de agentes.

        action descreve a motivação para auditoria (create|approve|suspend|
        retire|edit). Duplicidade de agent_id dentro da mesma versão é erro.
        """
        ids = [a.agent_id for a in agents]
        if len(ids) != len(set(ids)):
            raise ValueError("agent_id duplicado na nova versão")
        version = self._new_version()
        self._versions[version] = {a.agent_id: a for a in agents}
        self._current = version
        self._transitions.append(
            {"version": version, "action": action, "agent_id": agent_id}
        )
        return version

    # ------------------------------------------------------- lifecycle API

    def _mutate_lifecycle(self, agent_id: str, new_lifecycle: str, action: str) -> str:
        base = self.snapshot()  # versão atual
        if agent_id not in base:
            raise KeyError(f"agente inexistente: {agent_id}")
        old = base[agent_id]
        updated = AgentRecord(
            agent_id=old.agent_id,
            persona_id=old.persona_id,
            registry_version=self.current_version,
            capabilities=frozenset(sorted(old.capabilities)),
            runtime_class=old.runtime_class,
            lifecycle=new_lifecycle,
            prohibited_authorities=frozenset(sorted(old.prohibited_authorities)),
        )
        nxt = [a for aid, a in base.items() if aid != agent_id] + [updated]
        return self.create_version(nxt, action=action, agent_id=agent_id)

    def register(self, record: AgentRecord) -> str:
        base = list(self.snapshot().values()) if self._versions else []
        if any(a.agent_id == record.agent_id for a in base):
            raise ValueError(f"agente já registrado: {record.agent_id}")
        return self.create_version([*base, record], action="create",
                                   agent_id=record.agent_id)

    def approve(self, agent_id: str) -> str:
        return self._mutate_lifecycle(agent_id, "approved", "approve")

    def suspend(self, agent_id: str) -> str:
        return self._mutate_lifecycle(agent_id, "suspended", "suspend")

    def retire(self, agent_id: str) -> str:
        return self._mutate_lifecycle(agent_id, "retired", "retire")

    # --------------------------------------------------------- serialização

    def export_all(self) -> dict[str, Any]:
        """Estado completo (todas as versões + transições) para persistência."""
        if self._current is None:
            raise RuntimeError("registro vazio: nada a exportar")
        versions = {
            v: [
                {
                    "agent_id": a.agent_id,
                    "persona_id": a.persona_id,
                    "registry_version": a.registry_version,
                    "capabilities": sorted(a.capabilities),
                    "runtime_class": a.runtime_class,
                    "lifecycle": a.lifecycle,
                    "prohibited_authorities": sorted(a.prohibited_authorities),
                }
                for a in (self._versions[v][k] for k in sorted(self._versions[v]))
            ]
            for v in sorted(self._versions)
        }
        return {"current": self._current,
                "versions": versions,
                "transitions": list(self._transitions)}

    def to_json_dict(self, version: str | None = None) -> dict[str, Any]:
        snap = self.snapshot(version)
        return {
            "schema_version": self.current_version,
            "agents": [
                {
                    "agent_id": a.agent_id,
                    "persona_id": a.persona_id,
                    "capabilities": sorted(a.capabilities),
                    "runtime_class": a.runtime_class,
                    "lifecycle": a.lifecycle,
                    "prohibited_authorities": sorted(a.prohibited_authorities),
                }
                for a in (snap[k] for k in sorted(snap))
            ],
        }
