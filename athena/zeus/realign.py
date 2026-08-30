"""Z-REALIGN v4 — reconstrução limpa da separação de autoridade.

**Zeus** (`ZeusEligibilityRouter`): NÃO herda ZeusRouter. Implementa
elegibilidade pura a partir do registro público (current_version + snapshot).
Saída: `EligibilityResult` — especialista/persona + requisitos de runtime
(AgentRecord.runtime_class: local|frontier|either) + risco espelhado.
Nunca seleciona provider, modelo, CLI, modo ou runtime concreto.

**Nike** (`NikeRuntimeSelector`): consome EligibilityResult + providers
validados (`validate_providers`, fail-closed) + estado observado sanitizado +
veredito Aegis. Compara `provider.runtime_class` com o requisito do
especialista; retorna `provider.mode` inalterado como método de execução.
Nunca acessa atributos privados do Zeus; nunca altera escopo/risco/Aegis.

Sem shadow registry, sem strip-after-selection, sem varredura de código.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config_loader import load_observed, provider_eligible, validate_providers
from .contracts import REASON_CODES, AgentRecord, TaskRequest
from .registry import ZeusRegistry

REASON_RESOLVED = "RUNTIME_RESOLVED_BY_NIKE"
REASON_NO_PROVIDER = "ABSTAIN_NO_CAPABLE_PROVIDER"


# ------------------------------------------------------------------- Zeus

@dataclass(frozen=True)
class EligibilityResult:
    """Elegibilidade de especialista — a ÚNICA autoridade do Zeus."""
    task_signature: str
    registry_version: str
    eligible_specialist_id: str | None
    persona_id: str | None
    required_capabilities: tuple[str, ...]
    required_runtime_classes: tuple[str, ...]  # local|frontier|either(como entrada)
    risk_level: str
    reason_codes: tuple[str, ...]
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_signature": self.task_signature,
            "registry_version": self.registry_version,
            "eligible_specialist_id": self.eligible_specialist_id,
            "persona_id": self.persona_id,
            "required_capabilities": list(self.required_capabilities),
            "required_runtime_classes": list(self.required_runtime_classes),
            "risk_level": self.risk_level,
            "reason_codes": list(self.reason_codes),
            "confidence": self.confidence,
        }


def runtime_classes_of(agent: AgentRecord) -> tuple[str, ...]:
    """Requisito de runtime do especialista: AgentRecord.runtime_class.

    either mapeia para as duas classes; human_only etc. mapeiam para vazio
    (nenhum provider satisfaz — fluxo humano é regido por política).
    """
    if agent.runtime_class == "either":
        return ("local", "frontier")
    if agent.runtime_class in ("local", "frontier"):
        return (agent.runtime_class,)
    return ()


class ZeusEligibilityRouter:
    """Elegibilidade pura. Não herda ZeusRouter; não invoca rota de runtime."""

    def __init__(self, registry: ZeusRegistry,
                 confidence_threshold: float = 0.5) -> None:
        self._registry = registry
        self._threshold = confidence_threshold

    # APIs públicas do registro apenas:
    def version(self) -> str:
        return self._registry.current_version

    def snapshot(self) -> dict[str, AgentRecord]:
        return self._registry.snapshot()

    def eligibility(self, request: TaskRequest) -> EligibilityResult:
        sig = self._signature(request)
        version = self.version()
        snapshot = self.snapshot()
        reasons: list[str] = []

        # tag explícita: sinal forte, nunca contorna elegibilidade
        tagged = None
        if request.explicit_agent_tag:
            cand = snapshot.get(request.explicit_agent_tag)
            if cand is not None and cand.eligible() and self._covers(cand, request):
                tagged = cand

        authorities = [c.split(":", 1)[1] for c in request.required_capabilities
                       if c.startswith("authority:")]

        candidates: list[AgentRecord] = []
        capable_prohibiting = 0
        if tagged is not None:
            reasons.append("EXPLICIT_USER_TAG")
            if not any(a in tagged.prohibited_authorities for a in authorities):
                candidates = [tagged]
            elif authorities:
                capable_prohibiting = 1
        else:
            for agent in sorted(snapshot.values(), key=lambda a: a.agent_id):
                if not agent.eligible() or not self._covers(agent, request):
                    continue
                if any(a in agent.prohibited_authorities for a in authorities):
                    capable_prohibiting += 1
                    continue
                candidates.append(agent)

        if not candidates:
            # autoridade pedida é proibida por todos os capáveis → razão própria
            if capable_prohibiting and authorities:
                return EligibilityResult(
                    task_signature=sig, registry_version=version,
                    eligible_specialist_id=None, persona_id=None,
                    required_capabilities=tuple(request.required_capabilities),
                    required_runtime_classes=(), risk_level=request.risk_level,
                    reason_codes=("PROHIBITED_AUTHORITY_REQUESTED",),
                    confidence=0.9)


            suspended = [a for a in snapshot.values()
                         if a.lifecycle == "suspended" and self._covers(a, request)]
            reason = ("ABSTAIN_CAPABILITY_SUSPENDED" if suspended
                      else "ABSTAIN_NO_CAPABLE_AGENT")
            return EligibilityResult(
                task_signature=sig, registry_version=version,
                eligible_specialist_id=None, persona_id=None,
                required_capabilities=tuple(request.required_capabilities),
                required_runtime_classes=(), risk_level=request.risk_level,
                reason_codes=(reason,), confidence=0.8)

        if request.explicit_agent_tag:
            reasons.append("EXPLICIT_USER_TAG")
        reasons.append("CAPABILITY_MATCH")
        best = candidates[0]
        if best.persona_id.startswith(request.primary_domain.split(".")[0]):
            reasons.append("PERSONA_MATCH")
        if request.risk_level in ("high", "critical"):
            reasons.append("HIGH_RISK_HUMAN_REVIEW")

        confidence = 0.9 if tagged else 0.8
        if confidence < self._threshold:
            reasons.append("ABSTAIN_LOW_CONFIDENCE")
            return EligibilityResult(
                task_signature=sig, registry_version=version,
                eligible_specialist_id=None, persona_id=None,
                required_capabilities=tuple(request.required_capabilities),
                required_runtime_classes=(), risk_level=request.risk_level,
                reason_codes=tuple(reasons), confidence=confidence)

        return EligibilityResult(
            task_signature=sig, registry_version=version,
            eligible_specialist_id=best.agent_id, persona_id=best.persona_id,
            required_capabilities=tuple(request.required_capabilities),
            required_runtime_classes=runtime_classes_of(best),
            risk_level=request.risk_level,
            reason_codes=tuple(dict.fromkeys(reasons)),
            confidence=confidence)

    def _covers(self, agent: AgentRecord, request: TaskRequest) -> bool:
        real = {c for c in request.required_capabilities
                if not c.startswith("authority:")}
        return real <= set(agent.capabilities)

    @staticmethod
    def _signature(request: TaskRequest) -> str:
        from .router import task_signature
        return task_signature(request)


# ------------------------------------------------------------------ Nike

@dataclass(frozen=True)
class NikeDecision:
    """Resolução concreta de runtime — autoridade exclusiva da Nike."""
    task_signature: str
    specialist_id: str | None
    persona_id: str | None
    provider_id: str | None
    mode: str | None
    runtime_class: str | None
    model_id: str | None
    reason_codes: tuple[str, ...]
    confidence: float
    abstained: bool = False
    aegis_allowed: bool = True
    source_zeus_reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_signature": self.task_signature,
            "specialist_id": self.specialist_id,
            "persona_id": self.persona_id,
            "provider_id": self.provider_id,
            "mode": self.mode,
            "runtime_class": self.runtime_class,
            "model_id": self.model_id,
            "reason_codes": list(self.reason_codes),
            "confidence": self.confidence,
            "abstained": self.abstained,
            "aegis_allowed": self.aegis_allowed,
            "source_zeus_reasons": list(self.source_zeus_reasons),
        }


class NikeRuntimeSelector:
    """Resolver requisito → provider contra configuração validada."""

    def __init__(self, eligibility_router: ZeusEligibilityRouter,
                 providers_config: dict[str, Any] | None = None,
                 cache_dir: Path | None = None) -> None:
        self._zeus = eligibility_router
        # fail-closed: valida TODA a configuração antes de qualquer seleção
        if providers_config:
            validate_providers(providers_config)
        self._providers = dict(providers_config or {})
        from pathlib import Path as _P
        self._cache_dir = _P(cache_dir) if cache_dir else None

    def resolve(self, request: TaskRequest, *,
                aegis_allows: bool = True,
                capability_map: dict[str, bool] | None = None,
                direct_provider_id: str | None = None) -> NikeDecision:
        elig = self._zeus.eligibility(request)
        zeus_reasons = elig.reason_codes

        if elig.eligible_specialist_id is None:
            primary = zeus_reasons[0] if zeus_reasons else "ABSTAIN_NO_CAPABLE_AGENT"
            if primary not in REASON_CODES:
                primary = "ABSTAIN_NO_CAPABLE_AGENT"
            return NikeDecision(
                task_signature=elig.task_signature,
                specialist_id=None, persona_id=None,
                provider_id=None, mode=None, runtime_class=None,
                model_id=None,
                reason_codes=(primary, *zeus_reasons[1:]),
                confidence=elig.confidence, abstained=True,
                aegis_allowed=aegis_allows,
                source_zeus_reasons=zeus_reasons)

        observed = load_observed(self._cache_dir) if self._cache_dir else []
        obs_by_pid = {e["provider_id"]: e for e in observed
                      if isinstance(e, dict) and e.get("provider_id")}

        provider_ids = (
            (direct_provider_id,)
            if direct_provider_id is not None
            else tuple(sorted(self._providers))
        )
        for pid in provider_ids:
            if pid not in self._providers:
                continue
            spec = self._providers[pid]
            ok, _reason = provider_eligible(
                spec, obs_by_pid.get(pid),
                aegis_allows=aegis_allows,
                capability_ok=(capability_map or {}).get(pid, True))
            if not ok:
                continue
            # compatibilidade: runtime_class do provider deve satisfazer o
            # requisito do especialista; mode é devolvido inalterado
            if spec.get("runtime_class") not in elig.required_runtime_classes:
                continue
            return NikeDecision(
                task_signature=elig.task_signature,
                specialist_id=elig.eligible_specialist_id,
                persona_id=elig.persona_id,
                provider_id=pid,
                mode=spec["mode"],                       # inalterado
                runtime_class=spec.get("runtime_class"),
                model_id=spec.get("default_model"),
                reason_codes=(REASON_RESOLVED, *zeus_reasons),
                confidence=max(elig.confidence, 0.9),
                aegis_allowed=True,
                source_zeus_reasons=zeus_reasons)

        return NikeDecision(
            task_signature=elig.task_signature,
            specialist_id=elig.eligible_specialist_id,
            persona_id=elig.persona_id,
            provider_id=None, mode=None, runtime_class=None, model_id=None,
            reason_codes=(REASON_NO_PROVIDER, *elig.reason_codes),
            confidence=elig.confidence, abstained=True,
            aegis_allowed=aegis_allows,
            source_zeus_reasons=zeus_reasons)


def route_with_runtime(request: TaskRequest,
                       zeus: ZeusEligibilityRouter,
                       nike: NikeRuntimeSelector) -> NikeDecision:
    """Fluxo canônico pós-REALIGN: Zeus elegibilita → Nike resolve."""
    return nike.resolve(request)
