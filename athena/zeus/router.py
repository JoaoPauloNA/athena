"""Matcher e seletor do Zeus (Z-3/Z-4) — determinístico e fail-closed.

Z-3 capability matcher: casa capacidades pedidas com agentes `approved`.
Z-4 runtime selector: local vs frontier por regra determinística de risco;
abstenção quando nada cobre ou a confiança é insuficiente.

Themis (Z-6): só influencia a ordem entre candidatos igualmente capazes
quando recebe evidência estatística suficiente; caso contrário é ignorado
com reason code explícito — nunca silenciosamente.
"""

from __future__ import annotations

import hashlib
import json

from .contracts import (
    AgentRecord,
    TaskRequest,
    ZeusDecision,
    abstain,
)

# limiar mínimo de confiança para selecionar (abaixo: abstenção)
CONFIDENCE_THRESHOLD = 0.5

# risco que força frontier/human independentemente da tarefa
HIGH_RISK = frozenset({"high", "critical"})
HUMAN_ONLY_RISK = frozenset({"critical"})

CAPABILITY_WEIGHTS = {  # pesos simples e determinísticos
    "exact_domain": 0.4,
    "capability_coverage": 0.4,
    "explicit_tag": 0.2,
}


def task_signature(request: TaskRequest) -> str:
    """Assinatura estável da entrada (determinismo auditável)."""
    canonical = json.dumps(
        {
            "task_type": request.task_type,
            "primary_domain": request.primary_domain,
            "risk_level": request.risk_level,
            "required_capabilities": sorted(request.required_capabilities),
            "explicit_agent_tag": request.explicit_agent_tag,
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


class ZeusRouter:
    """Roteador determinístico entrada+registro -> decisão."""

    def __init__(
        self,
        registry,  # ZeusRegistry (evita import circular)
        *,
        confidence_threshold: float = CONFIDENCE_THRESHOLD,
        themis_scores: dict[str, float] | None = None,
        themis_sufficient: bool = False,
    ) -> None:
        self._registry = registry
        self._threshold = confidence_threshold
        # Themis: scores opcionais; sem flag de suficiência são ignorados
        self._themis: dict[str, float] | None = (
            dict(themis_scores) if (themis_sufficient and themis_scores) else None
        )
        self._themis_sufficient = bool(themis_sufficient)

    # ------------------------------------------------------------- núcleo

    def route(self, request: TaskRequest) -> ZeusDecision:
        sig = task_signature(request)
        version = self._registry.current_version
        snapshot = self._registry.snapshot(version)
        approved = [a for a in snapshot.values() if a.eligible()]

        reasons: list[str] = []
        confidence = 0.0

        # 1. autoridade proibida bloqueia antes de qualquer rota
        prohibited_hit = self._prohibited_requested(request, snapshot)
        if prohibited_hit is not None:
            return abstain(sig, version, ("PROHIBITED_AUTHORITY_REQUESTED",), 1.0)

        # 2. tag explícita do usuário: sinal forte, mas nunca contorna elegibilidade
        if request.explicit_agent_tag:
            tagged = next(
                (a for a in approved if a.agent_id == request.explicit_agent_tag), None
            )
            if tagged is not None and self._covers(tagged, request):
                return self._decide(sig, version, tagged, request,
                                    ["EXPLICIT_USER_TAG"], 0.95)
            if tagged is not None and not tagged.eligible():
                pass  # tratado abaixo como não-elegível
            reasons.append("EXPLICIT_USER_TAG")  # sinal presente, seguiu para matching

        # 3. matching por capacidade sobre aprovados; candidatos que proíbem
        # explicitamente uma autoridade pedida ("authority:X") saem da rota
        authorities = [c.split(":", 1)[1] for c in request.required_capabilities
                       if c.startswith("authority:")]
        candidates = []
        for a in approved:
            if not self._covers(a, request):
                continue
            if any(auth in a.prohibited_authorities for auth in authorities):
                continue  # não pode ser escolhido para o que declarou não fazer
            candidates.append((a, self._score(a, request)))

        if not candidates:
            # distinguir: suspenso vs inexistente
            suspended_capable = [
                a for a in snapshot.values()
                if a.lifecycle == "suspended" and self._covers(a, request)
            ]
            if suspended_capable:
                return abstain(sig, version, ("ABSTAIN_CAPABILITY_SUSPENDED",), 0.9)
            return abstain(sig, version, ("ABSTAIN_NO_CAPABLE_AGENT",), 0.8)

        # 4. Themis como desempate somente com evidência suficiente
        themis = self._themis or {}
        if self._themis:
            reasons.append("THEMIS_EVIDENCE_SUFFICIENT")
            candidates.sort(key=lambda pair: (
                -themis.get(pair[0].agent_id, 0.0), pair[0].agent_id))
        else:
            reasons.append("ABSTAIN_THEMIS_INSUFFICIENT")
            candidates.sort(key=lambda pair: pair[0].agent_id)  # ordem estável

        best, score = candidates[0]
        confidence = round(min(1.0, score), 4)
        reasons.append("CAPABILITY_MATCH")
        if best.persona_id == request.primary_domain:
            reasons.append("PERSONA_MATCH")

        if confidence < self._threshold:
            return abstain(sig, version, ("ABSTAIN_LOW_CONFIDENCE",), confidence)

        # 5. risco alto/crítico altera runtime, não bloqueia a recomendação
        if request.risk_level in HIGH_RISK:
            reasons.append("HIGH_RISK_HUMAN_REVIEW")
        return self._decide(sig, version, best, request, reasons, confidence)

    # ------------------------------------------------------------ helpers

    def _decide(self, sig, version, agent: AgentRecord, request: TaskRequest,
                reasons: list[str], confidence: float) -> ZeusDecision:
        reasons = list(reasons)
        runtime = agent.runtime_class
        if request.risk_level in HIGH_RISK or len(request.required_capabilities) > 3:
            if runtime == "local" or runtime == "either":
                runtime = "frontier"
                reasons.append("RUNTIME_FRONTIER_REQUIRED")
            else:
                reasons.append("RUNTIME_FRONTIER_REQUIRED")
        else:
            reasons.append(
                "RUNTIME_LOCAL_ELIGIBLE" if runtime == "local"
                else "RUNTIME_FRONTIER_REQUIRED" if runtime == "frontier"
                else "RUNTIME_LOCAL_ELIGIBLE"
            )
        if request.risk_level in HUMAN_ONLY_RISK:
            runtime = "human_only"
        deduped = tuple(dict.fromkeys(reasons))  # preserva ordem, remove duplicatas
        return ZeusDecision(
            task_signature=sig,
            registry_version=version,
            selected=True,
            agent_id=agent.agent_id,
            persona_id=agent.persona_id,
            model_hint=None,
            runtime_class=runtime,
            reason_codes=deduped,
            confidence=confidence,
        )

    def _covers(self, agent: AgentRecord, request: TaskRequest) -> bool:
        """Cobertura: capacidades reais pedidas presentes no agente.

        Entradas "authority:X" não são capacidades executáveis — são
        pedidos de autorização e são tratados pela checagem de proibição.
        """
        real = {c for c in request.required_capabilities
                if not c.startswith("authority:")}
        return real <= set(agent.capabilities)

    def _score(self, agent: AgentRecord, request: TaskRequest) -> float:
        parts = CAPABILITY_WEIGHTS
        domain = parts["exact_domain"] if agent.persona_id.startswith(
            request.primary_domain.split(".")[0]) else 0.0
        coverage = parts["capability_coverage"] * (
            len(set(request.required_capabilities) & set(agent.capabilities))
            / max(1, len(request.required_capabilities))
        ) if request.required_capabilities else parts["capability_coverage"]
        tag = parts["explicit_tag"] if (
            request.explicit_agent_tag == agent.agent_id) else 0.0
        return domain + coverage + tag

    @staticmethod
    def _prohibited_requested(request: TaskRequest, snapshot: dict[str, AgentRecord]) -> str | None:
        """Autoridade pedida ("authority:X") que TODOS os agentes aprovados
        capazes (cobrindo as capacidades não-authority da tarefa) proíbem."""
        authorities = [c.split(":", 1)[1] for c in request.required_capabilities
                       if c.startswith("authority:")]
        real_caps = tuple(c for c in request.required_capabilities
                          if not c.startswith("authority:"))
        candidates = [
            a for a in snapshot.values()
            if a.eligible() and set(real_caps) <= set(a.capabilities)
        ]
        if not candidates:
            return None  # sem candidato: deixa o fluxo normal abster-se
        for auth in authorities:
            if all(auth in a.prohibited_authorities for a in candidates):
                return auth
        return None
