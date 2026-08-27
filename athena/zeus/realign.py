"""Z-REALIGN (Onda 1 do fechamento): separação canônica de autoridade.

- Zeus = elegibilidade e recomendação de especialista. NUNCA executa; NUNCA
  escolhe provider/runtime concreto (model_hint fica permanentemente None na
  saída — seleção concreta é da Nike contra providers.json).
- Nike = resolução de requisito → provider/runtime via providers.json +
  desempate por Themis válido. NUNCA redefine escopo, risco ou política;
  herda a checagem Aegis do fluxo normal.
"""

from __future__ import annotations

from pathlib import Path

from .nike import NikeSelector  # reexport estável

__all__ = ["NikeSelector", "zeus_never_executes_check"]


def zeus_never_executes_check() -> bool:
    """Invariante Z-REALIGN: nenhuma superfície pública do pacote expõe
    execução, subprocesso, rede ou escrita fora dos contratos."""
    base = Path(__file__).resolve().parent
    forbidden = ("Popen", "subprocess", "socket(", "urlopen")
    for name in ("router.py", "registry.py", "nike.py"):
        src = (base / name).read_text()
        for bad in forbidden:
            if bad in src:
                return False
    return True
