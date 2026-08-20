"""Gate de validação da FATIA 0."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Stage:
    """Descrição de um estágio independente do gate."""

    name: str
    command: tuple[str, ...]
    accepted_return_codes: frozenset[int] = frozenset({0})


STAGES = (
    Stage("lint", ("ruff", "check", ".")),
    Stage("boundaries", ("lint-imports",)),
    Stage(
        "p0",
        ("pytest", "tests", "-m", "not regression"),
        frozenset({0, 5}),
    ),
)


def main() -> int:
    """Executar todos os estágios e retornar falha se qualquer um falhar."""
    failed = False
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join(
        (str(Path(sys.executable).parent), env.get("PATH", ""))
    )

    for stage in STAGES:
        try:
            result = subprocess.run(
                stage.command,
                check=False,
                capture_output=True,
                env=env,
                text=True,
            )
            passed = result.returncode in stage.accepted_return_codes
        except OSError:
            passed = False
        print(f"{stage.name}: {'PASS' if passed else 'FAIL'}")
        failed |= not passed

    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
