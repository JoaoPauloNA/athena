"""Pacote Clio — observabilidade local não bloqueante (CLIO-0)."""

from __future__ import annotations

from .contracts import (
    DEFAULT_LEVEL,
    ENV_GLOBAL_LEVEL,
    ENV_PROJECT_LEVEL,
    ENV_SECURITY_LEVEL,
    ENV_USER_LEVEL,
    LEVEL_COMPLETE,
    LEVEL_NONE,
    LEVEL_PARTIAL,
    LEVEL_TECHNICAL,
    ClioCounters,
    ContentProtectorContract,
    LevelContext,
    ProtectedEnvelope,
    TechnicalEvent,
)
from .emitter import ClioEmitter, build_clio_emitter
from .policy import resolve_level
from .store import ClioStore, resolve_state_dir

__all__ = [
    "DEFAULT_LEVEL",
    "ENV_GLOBAL_LEVEL",
    "ENV_PROJECT_LEVEL",
    "ENV_SECURITY_LEVEL",
    "ENV_USER_LEVEL",
    "LEVEL_COMPLETE",
    "LEVEL_NONE",
    "LEVEL_PARTIAL",
    "LEVEL_TECHNICAL",
    "ClioCounters",
    "ClioEmitter",
    "ClioStore",
    "ContentProtectorContract",
    "LevelContext",
    "ProtectedEnvelope",
    "TechnicalEvent",
    "build_clio_emitter",
    "resolve_level",
    "resolve_state_dir",
]
