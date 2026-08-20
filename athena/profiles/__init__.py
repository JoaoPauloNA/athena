"""Shim temporário de compatibilidade para os perfis fornecidos pelo Aegis.

A remoção deste pacote de compatibilidade fica reservada para uma fatia futura.
"""

from aegis.classification import classify_service_profile, resolve_service_profile
from aegis.contracts import FailureCondition, FallbackPolicy, ServiceProfile
from aegis.policy import (
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
