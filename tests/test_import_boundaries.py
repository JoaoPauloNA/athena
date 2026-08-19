"""Testes executáveis das fronteiras de importação do núcleo."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LINT_IMPORTS = Path(sys.executable).with_name("lint-imports")

IMPORT_LINTER_CONFIG = """\
[importlinter]
root_package = athena
include_external_packages = true

[importlinter:contract:core-layers]
name = core modules follow the allowed dependency layers and never import legado
type = layers
layers =
    (legado)
    athena.verifier | athena.mcp_server
    athena.router
    athena.bridge
    athena.execution | athena.registry | athena.lease | athena.profiles | athena.transport

[importlinter:contract:bridge-closed-edges]
name = bridge imports only execution and lease from lower core layers
type = forbidden
source_modules = athena.bridge
forbidden_modules =
    athena.registry
    athena.profiles
    athena.transport

[importlinter:contract:router-closed-edges]
name = router does not import closed lower-layer modules
type = forbidden
source_modules = athena.router
forbidden_modules =
    athena.registry
    athena.transport
"""


def _run_import_linter(directory: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (LINT_IMPORTS, "--no-cache", *arguments),
        cwd=directory,
        capture_output=True,
        check=False,
        text=True,
    )


def test_import_linter_rejects_dependency_against_layers(tmp_path: Path) -> None:
    shutil.copytree(PROJECT_ROOT / "athena", tmp_path / "athena")
    (tmp_path / ".importlinter").write_text(IMPORT_LINTER_CONFIG, encoding="utf-8")
    execution_module = tmp_path / "athena" / "execution" / "__init__.py"
    with execution_module.open("a", encoding="utf-8") as module_file:
        module_file.write("\nimport athena.router\n")

    result = _run_import_linter(tmp_path, "--config", ".importlinter")

    assert result.returncode != 0


def test_import_linter_rejects_dependency_on_legado(tmp_path: Path) -> None:
    shutil.copytree(PROJECT_ROOT / "athena", tmp_path / "athena")
    (tmp_path / ".importlinter").write_text(IMPORT_LINTER_CONFIG, encoding="utf-8")
    execution_module = tmp_path / "athena" / "execution" / "__init__.py"
    with execution_module.open("a", encoding="utf-8") as module_file:
        module_file.write("\nimport legado\n")

    result = _run_import_linter(tmp_path, "--config", ".importlinter")

    assert result.returncode != 0


def test_import_linter_accepts_repository_configuration() -> None:
    result = _run_import_linter(
        PROJECT_ROOT,
        "--config",
        str(PROJECT_ROOT / "pyproject.toml"),
    )

    assert result.returncode == 0, result.stdout + result.stderr
