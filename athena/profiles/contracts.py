"""Contratos públicos para classificação e fallback de perfis de serviço."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ServiceProfile(str, Enum):
    """Perfis de serviço reconhecidos pelo núcleo."""

    TEXT_GENERATION = "text_generation"
    CODE_AGENT = "code_agent"
    BUILD_TEST = "build_test"
    RESEARCH = "research"
    LOCAL_MODEL = "local_model"
    VERIFICATION = "verification"
    WORKSPACE_MUTATION = "workspace_mutation"
    AUTHENTICATED_EXTERNAL = "authenticated_external"
    UNKNOWN = "unknown"


class FailureCondition(str, Enum):
    """Condições terminais consideradas pela política de fallback."""

    TIMEOUT = "timeout"
    NETWORK_ERROR = "network_error"
    PROVIDER_ERROR = "provider_error"
    CANCELLATION = "cancellation"
    OTHER = "other"


@dataclass(frozen=True)
class FallbackPolicy:
    """Condições em que um perfil autoriza fallback automático."""

    profile: ServiceProfile
    automatic_fallback_on: frozenset[FailureCondition]

    def allows(self, condition: FailureCondition) -> bool:
        """Informar se a condição permite iniciar outra tentativa."""
        return condition in self.automatic_fallback_on
