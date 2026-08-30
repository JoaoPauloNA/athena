"""Resolução determinística de nível Clio com precedência e anti-elevação."""

from __future__ import annotations

import os
from collections.abc import Mapping

from .contracts import (
    DEFAULT_LEVEL,
    ENV_GLOBAL_LEVEL,
    ENV_PROJECT_LEVEL,
    ENV_SECURITY_LEVEL,
    ENV_USER_LEVEL,
    LEVEL_COMPLETE,
    LEVEL_RANK,
    VALID_LEVELS,
    LevelContext,
)


def _normalize_level(raw: str | None) -> str | None:
    if raw is None:
        return None
    cleaned = raw.strip().lower()
    if cleaned not in VALID_LEVELS:
        return None
    return cleaned


def _min_level(*levels: str) -> str:
    return min(levels, key=lambda level: LEVEL_RANK[level])


def resolve_level(
    context: LevelContext | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> str:
    """Aplicar precedência: segurança -> global -> projeto -> usuário -> sugestão MCP.

    Políticas superiores definem o teto autorizado; sugestão MCP só pode rebaixar.
    """
    source = env if env is not None else os.environ
    ctx = context or LevelContext()

    user = _normalize_level(ctx.user_level) or _normalize_level(
        source.get(ENV_USER_LEVEL)
    )
    project = _normalize_level(ctx.project_level) or _normalize_level(
        source.get(ENV_PROJECT_LEVEL)
    )
    global_level = _normalize_level(ctx.global_level) or _normalize_level(
        source.get(ENV_GLOBAL_LEVEL)
    )
    security = _normalize_level(ctx.security_level) or _normalize_level(
        source.get(ENV_SECURITY_LEVEL)
    )
    mcp_explicit = _normalize_level(ctx.mcp_suggestion)

    policy_levels = [
        level
        for level in (security, global_level, project, user)
        if level is not None
    ]
    policy = _min_level(*policy_levels) if policy_levels else DEFAULT_LEVEL

    if mcp_explicit is None:
        return policy
    return _min_level(policy, mcp_explicit)


def effective_emission_level(
    resolved: str,
    *,
    protector_available: bool,
) -> str:
    """Complete sem protetor falha fechado — emite só metadados técnicos."""
    if resolved == LEVEL_COMPLETE and not protector_available:
        return LEVEL_COMPLETE  # sinaliza indisponibilidade ao produtor
    return resolved


def complete_content_allowed(resolved: str, *, protector_available: bool) -> bool:
    return resolved == LEVEL_COMPLETE and protector_available
