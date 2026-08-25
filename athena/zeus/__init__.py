"""Zeus: recomendação de especialista (agente/persona/modelo). Nunca executa."""

from .contracts import (
    DECISION_SCHEMA_VERSION,
    REASON_CODES,
    REGISTRY_SCHEMA_VERSION,
    AgentRecord,
    TaskRequest,
    ZeusDecision,
    abstain,
)
from .registry import ZeusRegistry
from .router import CONFIDENCE_THRESHOLD, ZeusRouter, task_signature

__all__ = [
    "CONFIDENCE_THRESHOLD",
    "DECISION_SCHEMA_VERSION",
    "REASON_CODES",
    "REGISTRY_SCHEMA_VERSION",
    "AgentRecord",
    "TaskRequest",
    "ZeusDecision",
    "ZeusRegistry",
    "ZeusRouter",
    "abstain",
    "task_signature",
]
