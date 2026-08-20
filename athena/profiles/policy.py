"""Reexports de compatibilidade da política de fallback do Aegis."""

from aegis.policy import (
    FALLBACK_POLICIES,
    allows_automatic_fallback,
    get_fallback_policy,
)

__all__ = [
    "FALLBACK_POLICIES",
    "allows_automatic_fallback",
    "get_fallback_policy",
]
