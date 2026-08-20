"""Política de fallback automático por perfil de serviço."""

from __future__ import annotations

from .classification import classify_service_profile
from .contracts import FailureCondition, FallbackPolicy, ServiceProfile

_ERROR_CONDITIONS = frozenset(
    {
        FailureCondition.NETWORK_ERROR,
        FailureCondition.PROVIDER_ERROR,
        FailureCondition.OTHER,
    }
)
_STANDARD_CONDITIONS = _ERROR_CONDITIONS | {FailureCondition.TIMEOUT}

FALLBACK_POLICIES: dict[ServiceProfile, FallbackPolicy] = {
    ServiceProfile.TEXT_GENERATION: FallbackPolicy(
        ServiceProfile.TEXT_GENERATION, _STANDARD_CONDITIONS
    ),
    ServiceProfile.CODE_AGENT: FallbackPolicy(
        ServiceProfile.CODE_AGENT, _STANDARD_CONDITIONS
    ),
    ServiceProfile.BUILD_TEST: FallbackPolicy(
        ServiceProfile.BUILD_TEST, _STANDARD_CONDITIONS
    ),
    ServiceProfile.RESEARCH: FallbackPolicy(
        ServiceProfile.RESEARCH, _STANDARD_CONDITIONS
    ),
    ServiceProfile.LOCAL_MODEL: FallbackPolicy(
        ServiceProfile.LOCAL_MODEL, _STANDARD_CONDITIONS
    ),
    ServiceProfile.VERIFICATION: FallbackPolicy(
        ServiceProfile.VERIFICATION, _STANDARD_CONDITIONS
    ),
    ServiceProfile.WORKSPACE_MUTATION: FallbackPolicy(
        ServiceProfile.WORKSPACE_MUTATION, _ERROR_CONDITIONS
    ),
    ServiceProfile.AUTHENTICATED_EXTERNAL: FallbackPolicy(
        ServiceProfile.AUTHENTICATED_EXTERNAL, frozenset()
    ),
    ServiceProfile.UNKNOWN: FallbackPolicy(ServiceProfile.UNKNOWN, frozenset()),
}


def get_fallback_policy(profile: ServiceProfile | str | None) -> FallbackPolicy:
    """Obter a política; identificadores inválidos herdam a política fechada."""
    return FALLBACK_POLICIES[classify_service_profile(profile)]


def allows_automatic_fallback(
    profile: ServiceProfile | str | None,
    condition: FailureCondition | str,
) -> bool:
    """Decidir fallback; condições não reconhecidas são tratadas como ``other``."""
    try:
        resolved_condition = FailureCondition(condition)
    except (TypeError, ValueError):
        resolved_condition = FailureCondition.OTHER
    return get_fallback_policy(profile).allows(resolved_condition)
