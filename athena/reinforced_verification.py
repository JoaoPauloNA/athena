"""Política de verificação reforçada do Athena-MCP.

Decide, de forma pura e determinística, quando um resultado exige verificação
reforçada — isto é, quando a confiança no caminho de execução é menor do que a
do caminho padrão declarado para a tarefa.

A política **sinaliza**, não coage: nada aqui altera o resultado original, o
veredito do verificador, o exit code ou a política de fallback do combo. O
router apenas anexa a decisão ao resultado e emite um evento no Flight
Recorder. Quem decide o que fazer com o sinal é a camada de cima.

Regra (verdadeiro quando ao menos uma condição ocorre):

1. Foi usado um modelo de IA local.
2. A tarefa é complexa **e** foi usado um agente fora do padrão definido para
   essa tarefa.
3. O agente usado está fora do nível recomendado para a tarefa.
4. Houve fallback para um agente que não estava previsto na cadeia original.

Tarefa complexa sozinha **não** ativa verificação reforçada quando executada
inteiramente pela cadeia prevista e por agentes do nível recomendado.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from athena.flight_recorder import FlightRecorder

# Códigos de razão estáveis. São contrato: testes e consumidores externos
# dependem destes literais, então não devem ser renomeados sem migração.
REASON_LOCAL_MODEL = "local_model_used"
REASON_COMPLEX_NON_STANDARD = "complex_task_non_standard_agent"
REASON_OUTSIDE_RECOMMENDED_LEVEL = "agent_outside_recommended_level"
REASON_FALLBACK_OUTSIDE_CHAIN = "fallback_outside_original_chain"

REINFORCED_VERIFICATION_EVENT = "reinforced_verification_required"

# Providers cujo modelo roda na máquina do usuário.
LOCAL_MODEL_PROVIDER_IDS: frozenset[str] = frozenset({"ollama", "goose"})

# Perfil de serviço que, por definição, representa execução de modelo local.
LOCAL_MODEL_SERVICE_PROFILE = "local_model"

# Nível de modelo recomendado por complexidade da tarefa. Espelha
# `athena.recommend._MAX_WEIGHT_BY_COMPLEXITY`; é declarado aqui porque é uma
# decisão desta política, não um detalhe de implementação do recomendador.
RECOMMENDED_WEIGHT_BY_COMPLEXITY: dict[str, str] = {
    "simple": "light",
    "medium": "medium",
    "complex": "heavy",
}

_WEIGHT_ORDER: dict[str, int] = {"light": 0, "medium": 1, "heavy": 2}

COMPLEX_TASK = "complex"


@dataclass(frozen=True)
class ReinforcedVerificationContext:
    """Contexto de uma tentativa/cadeia avaliado pela política.

    Campos desconhecidos (``None`` ou vazios) nunca ativam verificação
    reforçada: a política só dispara com evidência positiva. Isso preserva o
    comportamento de chamadas legadas que não fornecem o contexto completo.
    """

    used_provider_id: str
    used_model: str | None = None
    used_weight: str | None = None
    task_complexity: str | None = None
    recommended_weight: str | None = None
    standard_provider_ids: tuple[str, ...] = ()
    original_chain: tuple[str, ...] = ()
    is_fallback: bool = False
    used_local_model: bool | None = None
    service_profile_id: str | None = None

    def resolve_local_model(self) -> bool:
        """Modelo local por override explícito, provider conhecido ou perfil."""
        if self.used_local_model is not None:
            return bool(self.used_local_model)
        if self.used_provider_id in LOCAL_MODEL_PROVIDER_IDS:
            return True
        return self.service_profile_id == LOCAL_MODEL_SERVICE_PROFILE

    def resolve_recommended_weight(self) -> str | None:
        """Nível recomendado explícito ou derivado da complexidade."""
        if self.recommended_weight is not None:
            return self.recommended_weight
        if self.task_complexity is None:
            return None
        return RECOMMENDED_WEIGHT_BY_COMPLEXITY.get(self.task_complexity)


@dataclass(frozen=True)
class ReinforcedVerificationDecision:
    """Resultado da política: sinal + razões auditáveis."""

    requires_reinforced_verification: bool
    reasons: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "requires_reinforced_verification": self.requires_reinforced_verification,
            "reasons": list(self.reasons),
            "details": dict(self.details),
        }


def evaluate_reinforced_verification(
    context: ReinforcedVerificationContext,
) -> ReinforcedVerificationDecision:
    """Aplica a regra de verificação reforçada sobre o contexto da tentativa.

    Função pura: sem I/O, sem estado global, sem efeitos colaterais. Todas as
    razões aplicáveis são preservadas (não há short-circuit), para que o
    registro forense mostre todos os motivos simultâneos.
    """
    reasons: list[str] = []
    details: dict[str, Any] = {}

    # 1. Modelo de IA local.
    if context.resolve_local_model():
        reasons.append(REASON_LOCAL_MODEL)
        details[REASON_LOCAL_MODEL] = {
            "provider_id": context.used_provider_id,
            "model": context.used_model,
            "service_profile": context.service_profile_id,
        }

    # 2. Tarefa complexa E agente fora do padrão definido para a tarefa.
    # Sem padrão declarado não há como julgar desvio: não dispara.
    if (
        context.task_complexity == COMPLEX_TASK
        and context.standard_provider_ids
        and context.used_provider_id not in context.standard_provider_ids
    ):
        reasons.append(REASON_COMPLEX_NON_STANDARD)
        details[REASON_COMPLEX_NON_STANDARD] = {
            "task_complexity": context.task_complexity,
            "used_provider_id": context.used_provider_id,
            "standard_provider_ids": list(context.standard_provider_ids),
        }

    # 3. Agente fora do nível recomendado para a tarefa.
    # Desvio em qualquer direção conta: sub-dimensionar arrisca alegação falsa,
    # super-dimensionar indica que o plano da tarefa não foi seguido. Níveis
    # desconhecidos não disparam.
    recommended_weight = context.resolve_recommended_weight()
    used_weight = context.used_weight
    if (
        recommended_weight is not None
        and used_weight is not None
        and used_weight != recommended_weight
    ):
        reasons.append(REASON_OUTSIDE_RECOMMENDED_LEVEL)
        details[REASON_OUTSIDE_RECOMMENDED_LEVEL] = {
            "used_weight": used_weight,
            "recommended_weight": recommended_weight,
            "direction": _weight_direction(used_weight, recommended_weight),
            "task_complexity": context.task_complexity,
            "model": context.used_model,
        }

    # 4. Fallback para agente fora da cadeia original.
    # Cobre também override de cadeia por continuação do orquestrador: em ambos
    # os casos executou-se um agente que o plano original não previa.
    if context.original_chain and context.used_provider_id not in context.original_chain:
        reasons.append(REASON_FALLBACK_OUTSIDE_CHAIN)
        details[REASON_FALLBACK_OUTSIDE_CHAIN] = {
            "used_provider_id": context.used_provider_id,
            "original_chain": list(context.original_chain),
            "is_fallback": context.is_fallback,
        }

    return ReinforcedVerificationDecision(
        requires_reinforced_verification=bool(reasons),
        reasons=reasons,
        details=details,
    )


def _weight_direction(used_weight: str, recommended_weight: str) -> str:
    """Classifica o desvio de nível como abaixo/acima/indeterminado."""
    used_rank = _WEIGHT_ORDER.get(used_weight)
    recommended_rank = _WEIGHT_ORDER.get(recommended_weight)
    if used_rank is None or recommended_rank is None:
        return "unknown"
    if used_rank < recommended_rank:
        return "below_recommended"
    if used_rank > recommended_rank:
        return "above_recommended"
    return "equal"


def emit_reinforced_verification_event(
    decision: ReinforcedVerificationDecision,
    *,
    execution_id: str,
    combo_id: str | None = None,
    provider_id: str | None = None,
    attempted_chain: list[str] | None = None,
    original_chain: list[str] | None = None,
    attempt_id: str | None = None,
    logs_dir: Any | None = None,
) -> None:
    """Registra ``reinforced_verification_required`` no Flight Recorder.

    Só emite quando a verificação reforçada é exigida. Falhas de escrita são
    absorvidas pelo próprio Flight Recorder (write failures são não-fatais),
    então esta função nunca interrompe a execução do combo.
    """
    if not decision.requires_reinforced_verification:
        return
    recorder = FlightRecorder(
        logs_dir=logs_dir,
        execution_id=execution_id,
        attempt_id=attempt_id or f"reinforced-{uuid.uuid4().hex[:12]}",
        provider=provider_id or "router",
        profile="combo",
        transport="local",
    )
    recorder.record_event(
        REINFORCED_VERIFICATION_EVENT,
        requires_reinforced_verification=True,
        reasons=list(decision.reasons),
        details=decision.details,
        combo_id=combo_id,
        provider_id=provider_id,
        attempted_chain=list(attempted_chain or []),
        original_chain=list(original_chain or []),
    )
