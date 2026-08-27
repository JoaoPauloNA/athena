"""CFG-0: schemas dos arquivos de configuração (`~/.athena/`).

Contrato congelado antes de código. Formas derivadas de
Athena-Configuracao-e-Modo-de-Execucao (Vault, decisão canônica).

Invariantes:
- segredo só como secret_ref;
- estado desejado (*.json versionado) ≠ observado (cache/, descartável);
- snapshot.json = unidade atômica: manifesto + hash de cada parte.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONFIG_SCHEMA_VERSION = "athena.config.v1"

ALLOWED_MODES = ("agent_cli", "api", "local", "remote")
ALLOWED_ADMIN_STATES = ("DISCOVERED", "ENABLED", "HEALTHY", "APPROVED")

_PART_FILES = ("providers.json", "functions.json")


def _fail(msg: str) -> None:
    raise ValueError(f"config inválida: {msg}")


def validate_providers(doc: dict[str, Any]) -> None:
    if not isinstance(doc, dict) or not doc:
        _fail("providers.json deve ser objeto não vazio")
    for pid, spec in doc.items():
        if not re.fullmatch(r"[a-z0-9-]+", pid):
            _fail(f"provider_id inválido: {pid}")
        if not isinstance(spec, dict):
            _fail(f"{pid}: especificação deve ser objeto")
        mode = spec.get("mode")
        if mode not in ALLOWED_MODES:
            _fail(f"{pid}: mode deve ser um de {ALLOWED_MODES}")
        # segredo: nunca valor, só referência
        if "secret" in spec or "api_key" in spec:
            _fail(f"{pid}: campo de segredo proibido; use 'secret_ref'")
        sr = spec.get("secret_ref")
        if sr is not None and not isinstance(sr, str):
            _fail(f"{pid}: secret_ref deve ser string")
        url = spec.get("base_url")
        if url is not None and mode in ("api", "local") \
                and not re.match(r"^https?://", str(url)):
            _fail(f"{pid}: base_url inválida")
        cmd = spec.get("command")
        if mode == "agent_cli" and (not isinstance(cmd, str) or not cmd):
            _fail(f"{pid}: modo agent_cli exige 'command'")


def validate_functions(doc: dict[str, Any]) -> None:
    if not isinstance(doc, dict):
        _fail("functions.json deve ser objeto")
    for fname, spec in doc.items():
        if not isinstance(spec, dict) or "specialist" not in spec:
            _fail(f"{fname}: exige campo 'specialist'")
        ms = spec.get("min_status")
        if ms is not None and ms not in ("approved", "candidate"):
            _fail(f"{fname}: min_status só approved|candidate")


@dataclass(frozen=True)
class SnapshotManifest:
    schema_version: str
    parts: dict[str, str]   # nome do arquivo → sha256
    extras: dict[str, str]  # recursos adicionais declarados (ex.: personas) → sha256


def part_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_manifest(config_dir: Path,
                   extra_parts: dict[str, Path] | None = None) -> SnapshotManifest:
    """Construir manifesto a partir das partes presentes no diretório."""
    parts: dict[str, str] = {}
    for name in _PART_FILES:
        p = config_dir / name
        parts[name] = part_hash(p)
    extras = {k: part_hash(v) for k, v in (extra_parts or {}).items()}
    return SnapshotManifest(CONFIG_SCHEMA_VERSION, parts, extras)


def write_snapshot(config_dir: Path, manifest: SnapshotManifest) -> None:
    payload = {
        "schema_version": manifest.schema_version,
        "parts": manifest.parts,
        "extras": manifest.extras,
    }
    (config_dir / "snapshot.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1))


def load_config(config_dir: Path) -> dict[str, Any]:
    """Carga atômica CFG-1.

    Regras (canônicas):
    1. snapshot.json ausente/inválido → recusa total (caller decide fallback);
    2. qualquer hash divergente → recusa a carga INTEIRA;
    3. recarga parcial é proibida — ou carrega tudo, ou nada;
    4. referência quebrada (função→especialista; especialista→provider) recusa.
    """
    snap_path = config_dir / "snapshot.json"
    try:
        manifest_raw = json.loads(snap_path.read_text())
    except FileNotFoundError:
        raise ConfigLoadError("snapshot.json ausente")
    except json.JSONDecodeError as exc:
        raise ConfigLoadError(f"snapshot.json inválido: {exc}")

    if manifest_raw.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise ConfigLoadError("schema_version divergente")

    declared: dict[str, str] = manifest_raw.get("parts", {})
    extras: dict[str, str] = manifest_raw.get("extras", {})

    loaded_parts: dict[str, Any] = {}
    for name, expected_hash in {**declared, **extras}.items():
        p = config_dir / name
        try:
            content = p.read_bytes()
        except FileNotFoundError:
            raise ConfigLoadError(f"parte declarada ausente: {name}")
        actual = hashlib.sha256(content).hexdigest()
        if actual != expected_hash:
            # DIVERGÊNCIA: recusa a carga inteira — nada é carregado parcialmente
            raise ConfigLoadError(f"hash divergente em {name}")

    # hashes conferem; agora parsear e validar as partes
    providers_doc = json.loads((config_dir / "providers.json").read_text())
    functions_doc = json.loads((config_dir / "functions.json").read_text())
    validate_providers(providers_doc)
    validate_functions(functions_doc)

    # resolver referências function → specialist → provider
    persona_dirs = {p.parent.name for p in config_dir.glob("personas/*/*")}
    for fname, spec in functions_doc.items():
        specialist = spec["specialist"]
        if specialist not in persona_dirs:
            raise ConfigLoadError(
                f"{fname}: especialista '{specialist}' sem bundle em personas/")

    loaded_parts["providers"] = providers_doc
    loaded_parts["functions"] = functions_doc

    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "parts": loaded_parts,
        "manifest": {"parts": declared, "extras": extras},
    }


class ConfigLoadError(Exception):
    """Recusa atômica: chamador preserva o snapshot anterior válido."""
