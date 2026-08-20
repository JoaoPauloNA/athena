"""Perfis de execução do Athena MCP."""

from .classification import classify_service_profile, resolve_service_profile
from .contracts import FailureCondition, FallbackPolicy, ServiceProfile
from .policy import (
    FALLBACK_POLICIES,
    allows_automatic_fallback,
    get_fallback_policy,
)

__all__ = [
    "FALLBACK_POLICIES",
    "FailureCondition",
    "FallbackPolicy",
    "ServiceProfile",
    "allows_automatic_fallback",
    "classify_service_profile",
    "get_fallback_policy",
    "resolve_service_profile",
]
