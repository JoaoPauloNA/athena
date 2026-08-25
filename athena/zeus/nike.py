"""Nike: recomendação de executor guiada por Themis — implementada como
extensão determinística do ZeusRouter (decisão canônica de fronteira).

Regras:
- Só considera scores Themis VÁLIDOS (>= MIN_EPISODES_FOR_VALID);
  caso contrário, cai no comportamento padrão do Zeus (ordem estável) e
  registra ABSTAIN_THEMIS_INSUFFICIENT.
- Nunca executa; nunca contorna Aegis.
"""

from __future__ import annotations

from .contracts import TaskRequest
from .registry import ZeusRegistry
from .router import ZeusRouter


class NikeSelector(ZeusRouter):
    """Zeus + desempate por nota válida do Themis."""

    def __init__(self, registry: ZeusRegistry,
                 themis_scores: dict[str, dict] | None = None) -> None:
        # themis_scores: {"agent_key": score_dict_do_themis}
        valid = {}
        for key, s in (themis_scores or {}).items():
            if s.get("valid"):
                valid[key] = float(s["final_score"])
        super().__init__(registry,
                         themis_scores=valid or None,
                         themis_sufficient=bool(valid))
        self._themis_detail = dict(themis_scores or {})

    def route_with_report(self, request: TaskRequest) -> dict:
        decision = self.route(request)
        return {
            "decision": decision.to_dict(),
            "themis_consulted": bool(self._themis),
            "themis_valid_keys": sorted(
                k for k, s in self._themis_detail.items() if s.get("valid")),
        }
