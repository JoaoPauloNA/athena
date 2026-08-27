"""CFG-3: descoberta de CLIs/providers SEM habilitação automática.

Regra canônica: o que a máquina reporta vai para `cache/inventory.json`
(estado OBSERVADO, descartável). Habilitar/aprovar é decisão administrativa
que grava em `providers.json` (estado DESEJADO). Descoberta nunca habilita:
DISCOVERED ≠ ENABLED ≠ HEALTHY ≠ APPROVED.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CANDIDATES = {
    "claude-cli": "claude",
    "codex-cli": "codex",
    "ollama": "ollama",
}


@dataclass
class DiscoveryReport:
    discovered: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(
            {"schema_version": "athena.inventory.v1",
             "observed_at_state": "DISCOVERED-only",
             "entries": self.discovered},
            ensure_ascii=False, indent=1)


def discover(cache_dir: Path,
             extra_candidates: dict[str, str] | None = None) -> DiscoveryReport:
    """Detectar CLIs instalados e registrar como DISCOVERED em cache/.

    Nunca escreve em providers.json; nunca marca ENABLED/HEALTHY/APPROVED.
    """
    out = DiscoveryReport()
    for cid, cmd in {**CANDIDATES, **(extra_candidates or {})}.items():
        path = shutil.which(cmd)
        if path is not None:
            out.discovered.append({
                "cli_id": cid,
                "command_resolved": path,
                "state": "DISCOVERED",   # único estado que descoberta concede
            })
    _save_observed(cache_dir, out)
    return out


def _save_observed(cache_dir: Path, report: DiscoveryReport) -> None:
    """Gravar exclusivamente na área observada (cache/, descartável)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "inventory.json").write_text(report.to_json())
