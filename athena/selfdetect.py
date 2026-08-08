from __future__ import annotations

import os
from typing import Optional

from athena.config import SELF_PROVIDER_ENV


def detect_self_provider(provider_ids: "tuple[str, ...]") -> Optional[str]:
    """Detecta qual provider é o próprio host orquestrador, se possível.

    Ordem de prioridade:
    1. Override explícito via env var ATHENA_SELF_PROVIDER (funciona pra qualquer host).
    2. Detecção automática por assinatura de ambiente conhecida (hoje só Claude Code,
       via CLAUDECODE=1 — confirmado que o processo filho herda essa env var).
    """
    override = os.environ.get(SELF_PROVIDER_ENV)
    if override and override in provider_ids:
        return override

    if os.environ.get("CLAUDECODE") == "1" and "claude" in provider_ids:
        return "claude"

    return None
