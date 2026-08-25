"""Contratos versionados do Zeus (Z-1) — recomendação de especialista.

Zeus RECOMENDA agente/persona/modelo. NUNCA executa. A autorização de
execução permanece no Aegis; a validação do resultado, no Evidence Gate;
a observação, na Moiras (shadow-only).

Vocabulário congelado (v1):
- reason_codes estáveis para toda seleção/abstenção (auditoria e teste).
- Determinismo: mesma entrada + mesma versão de registro => mesma saída.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

REGISTRY_SCHEMA_VERSION = "zeus.registry.v1"
DECISION_SCHEMA_VERSION = "zeus.decision.v1"

# ------------------------------------------------------------------ códigos

REASON_CODES = {
    # seleção
    "EXPLICIT_USER_TAG": "tag explícita do usuário escolheu o agente",
    "CAPABILITY_MATCH": "capacidade declarada cobre a tarefa",
    "PERSONA_MATCH": "persona operacional cobre o domínio da tarefa",
    "RUNTIME_LOCAL_ELIGIBLE": "tarefa elegível a runtime local",
    "RUNTIME_FRONTIER_REQUIRED": "contexto/risco exige runtime de fronteira",
    "THEMIS_EVIDENCE_SUFFICIENT": "nota Themis com evidência estatística suficiente",
    # abstenção
    "ABSTAIN_LOW_CONFIDENCE": "confiança abaixo do limiar; não adivinhar",
    "ABSTAIN_AMBIGUOUS_TASK": "tarefa ambígua demais para classificar",
    "ABSTAIN_NO_CAPABLE_AGENT": "nenhum agente aprovado cobre a capacidade",
    "ABSTAIN_CAPABILITY_SUSPENDED": "único agente capaz está suspenso",
    "ABSTAIN_THEMIS_INSUFFICIENT": "Themis sem evidência suficiente; ignorado",
    # restrições
    "HIGH_RISK_HUMAN_REVIEW": "risco alto exige revisão humana independente da rota",
    "PROHIBITED_AUTHORITY_REQUESTED": "tarefa pede autoridade proibida à persona",
}

# ordem canônica das razões na decisão (determinismo de serialização)
REASON_ORDER = [
    "PROHIBITED_AUTHORITY_REQUESTED",
    "ABSTAIN_LOW_CONFIDENCE",
    "ABSTAIN_AMBIGUOUS_TASK",
    "ABSTAIN_NO_CAPABLE_AGENT",
    "ABSTAIN_CAPABILITY_SUSPENDED",
    "ABSTAIN_THEMIS_INSUFFICIENT",
    "EXPLICIT_USER_TAG",
    "HIGH_RISK_HUMAN_REVIEW",
    "PERSONA_MATCH",
    "CAPABILITY_MATCH",
    "THEMIS_EVIDENCE_SUFFICIENT",
    "RUNTIME_FRONTIER_REQUIRED",
    "RUNTIME_LOCAL_ELIGIBLE",
]


@dataclass(frozen=True, slots=True)
class AgentRecord:
    """Entrada do registro de agentes/personas (imutável por versão)."""

    agent_id: str                    # ex.: "claude-code", "cx2", "ollama-local"
    persona_id: str                  # ex.: "software.engineer.backend.v1"
    registry_version: str            # versão do registro que criou esta entrada
    capabilities: frozenset[str]     # capacidades declaradas
    runtime_class: str               # "local" | "frontier" | "either"
    lifecycle: str = "experimental"  # experimental|approved|suspended|retired
    prohibited_authorities: frozenset[str] = field(default_factory=frozenset)

    ALLOWED_LIFECYCLE = ("experimental", "approved", "suspended", "retired")
    ALLOWED_RUNTIME = ("local", "frontier", "either")

    def __post_init__(self) -> None:
        if not self.agent_id or not isinstance(self.agent_id, str):
            raise TypeError("agent_id deve ser string não vazia")
        if not self.persona_id or not isinstance(self.persona_id, str):
            raise TypeError("persona_id deve ser string não vazia")
        if self.lifecycle not in self.ALLOWED_LIFECYCLE:
            raise ValueError(f"lifecycle inválido: {self.lifecycle}")
        if self.runtime_class not in self.ALLOWED_RUNTIME:
            raise ValueError(f"runtime_class inválido: {self.runtime_class}")
        for cap in self.capabilities:
            if not isinstance(cap, str) or not cap:
                raise ValueError("capabilities devem ser strings não vazias")

    def eligible(self) -> bool:
        """Somente agentes approved são roteáveis; experimental nunca é."""
        return self.lifecycle == "approved"


@dataclass(frozen=True, slots=True)
class TaskRequest:
    """Pedido de roteamento: classificação já feita pelo chamador."""

    task_type: str
    primary_domain: str
    risk_level: str                       # low|medium|high|critical
    required_capabilities: tuple[str, ...]
    explicit_agent_tag: str | None = None  # sinal do usuário; nunca autoridade


@dataclass(frozen=True, slots=True)
class ZeusDecision:
    """Saída determinística do roteador. `agent=None` significa abstenção."""

    task_signature: str                # assinatura determinística da entrada
    registry_version: str
    selected: bool
    agent_id: str | None
    persona_id: str | None
    model_hint: str | None             # dica de modelo; Aegis/Athena decidem executor final
    runtime_class: str | None          # local|frontier|human_only
    reason_codes: tuple[str, ...]
    confidence: float

    ALLOWED_RUNTIME_OUT = ("local", "frontier", "human_only")

    def __post_init__(self) -> None:
        if not self.selected and self.agent_id is not None:
            raise ValueError("abstenção não pode carregar agent_id")
        if self.selected and (not self.agent_id or not self.persona_id):
            raise ValueError("seleção requer agent_id e persona_id")
        if self.runtime_class is not None and self.runtime_class not in self.ALLOWED_RUNTIME_OUT:
            raise ValueError(f"runtime_class de saída inválido: {self.runtime_class}")
        unknown = [r for r in self.reason_codes if r not in REASON_CODES]
        if unknown:
            raise ValueError(f"reason_codes desconhecidos: {unknown}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence fora de [0,1]")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": DECISION_SCHEMA_VERSION,
            "task_signature": self.task_signature,
            "registry_version": self.registry_version,
            "selected": self.selected,
            "agent_id": self.agent_id,
            "persona_id": self.persona_id,
            "model_hint": self.model_hint,
            "runtime_class": self.runtime_class,
            "reason_codes": sorted(self.reason_codes, key=REASON_ORDER.index),
            "confidence": round(self.confidence, 4),
        }


def abstain(task_signature: str, registry_version: str,
            reasons: tuple[str, ...], confidence: float) -> ZeusDecision:
    """Fábrica canônica de abstenção (fail-closed)."""
    allowed = ("ABSTAIN_", "PROHIBITED_AUTHORITY_REQUESTED")
    safe = tuple(r for r in reasons if r.startswith(allowed[0]) or r in allowed[1:])
    if not safe:
        raise ValueError("abstain exige reason codes de abstenção ou proibição")
    return ZeusDecision(
        task_signature=task_signature,
        registry_version=registry_version,
        selected=False, agent_id=None, persona_id=None, model_hint=None,
        runtime_class=None, reason_codes=safe, confidence=confidence,
    )
