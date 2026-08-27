"""CFG-0A: contrato de estados corrigido — desejado ≠ observado.

**Desejado** (`providers.json`, versionado, entra no snapshot):
- enabled: bool
- approved: bool
- mode: agent_cli | api | local | remote   (modo é do PROVIDER)
- runtime_class: local | frontier          (classe é requisito de execução)
- secret_ref: str (apenas referência; nunca valor)

**Observado** (`cache/inventory.json`, descartável e reconstruível):
- discovered: bool
- healthy: bool
- health_checked_at / health_result (sanitizado)

Elegibilidade de execução exige TUDO:
enabled AND approved AND healthy AND capability compatível
AND Aegis permite. DISCOVERED sozinho nunca elegibiliza.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

CONFIG_SCHEMA_VERSION = "athena.config.v1.1"

ALLOWED_MODES = ("agent_cli", "api", "local", "remote")
ALLOWED_RUNTIME_CLASSES = ("local", "frontier")

_PART_FILES = ("providers.json", "functions.json")


def _fail(msg: str) -> None:
    raise ValueError(f"config inválida: {msg}")


# ------------------------------------------------------------------ desired

def validate_provider_spec(pid: str, spec: dict[str, Any]) -> None:
    if not re.fullmatch(r"[a-z0-9-]+", pid):
        _fail(f"provider_id inválido: {pid}")
    if not isinstance(spec, dict):
        _fail(f"{pid}: especificação deve ser objeto")

    mode = spec.get("mode")
    if mode not in ALLOWED_MODES:
        _fail(f"{pid}: mode deve ser um de {ALLOWED_MODES}")

    # runtime_class nunca recebe modo misturado
    rc = spec.get("runtime_class")
    if rc not in ALLOWED_RUNTIME_CLASSES:
        _fail(f"{pid}: runtime_class deve ser local|frontier")
    if isinstance(rc, str) and rc in ("agent_cli", "api", "remote"):
        _fail(f"{pid}: provider execution mode não pode ser usado como runtime_class")

    for flag in ("enabled", "approved"):
        if not isinstance(spec.get(flag), bool):
            _fail(f"{pid}: '{flag}' booleano obrigatório")

    # segredo: referência apenas; qualquer campo de valor é rejeitado
    if set(spec) & {"secret", "api_key", "apikey", "token", "password"}:
        _fail(f"{pid}: campo de segredo proibido; use 'secret_ref'")
    sr = spec.get("secret_ref")
    if sr is not None and (not isinstance(sr, str) or ":" not in sr):
        _fail(f"{pid}: secret_ref deve ser 'scheme:item'")

    url = spec.get("base_url")
    if url is not None and mode in ("api", "local") \
            and not re.match(r"^https?://", str(url)):
        _fail(f"{pid}: base_url inválida")

    cmd = spec.get("command")
    if mode == "agent_cli" and (not isinstance(cmd, str) or not cmd.strip()):
        _fail(f"{pid}: agent_cli exige 'command'")


def validate_providers(doc: dict[str, Any]) -> None:
    if not isinstance(doc, dict) or not doc:
        _fail("providers.json deve ser objeto não vazio")
    for pid, spec in doc.items():
        validate_provider_spec(pid, spec)


def validate_functions(doc: dict[str, Any]) -> None:
    if not isinstance(doc, dict):
        _fail("functions.json deve ser objeto")
    for fname, spec in doc.items():
        if not isinstance(spec, dict) or "specialist" not in spec:
            _fail(f"{fname}: exige campo 'specialist'")
        ms = spec.get("min_status")
        if ms is not None and ms not in ("approved", "candidate"):
            _fail(f"{fname}: min_status só approved|candidate")


# ----------------------------------------------------------------- observed

_OBSERVED_KEYS = ("discovered", "healthy")


def validate_inventory(doc: dict[str, Any]) -> None:
    """Estado observado NUNCA entra em snapshot; validação independente."""
    if not isinstance(doc, dict):
        _fail("cache/inventory.json deve ser objeto")
    def _reject_admin(obj: dict[str, Any], where: str) -> None:
        if set(obj) & {"enabled", "approved"}:
            _fail(f"{where}: observado não pode carregar estado administrativo (desejado)")

    _reject_admin(doc, "inventory")
    for entry in doc.get("entries", []):
        if isinstance(entry, dict):
            _reject_admin(entry, f"entry {entry.get('provider_id') or entry.get('cli_id')}")
        for k in ("cli_id", "provider_id"):
            v = entry.get(k)
            if v is not None and not isinstance(v, str):
                _fail(f"inventory: {k} deve ser string")
        for k in _OBSERVED_KEYS:
            if k in entry and not isinstance(entry[k], bool):
                _fail(f"inventory: '{k}' booleano")


# ------------------------------------------------------------- snapshot

def part_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_manifest(config_dir: Path,
                   extra_parts: dict[str, Path] | None = None) -> dict[str, Any]:
    parts = {name: part_hash(config_dir / name) for name in _PART_FILES}
    extras = {k: part_hash(v) for k, v in (extra_parts or {}).items()}
    return {"schema_version": CONFIG_SCHEMA_VERSION,
            "parts": parts, "extras": extras}


def write_snapshot(config_dir: Path, manifest: dict[str, Any]) -> None:
    (config_dir / "snapshot.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1))


class ConfigLoadError(Exception):
    """Recusa atômica: chamador preserva o snapshot anterior válido."""


def load_config(config_dir: Path) -> dict[str, Any]:
    """Carga atômica — estados desejados APENAS. Observado fica em cache/."""
    snap_path = config_dir / "snapshot.json"
    try:
        manifest_raw = json.loads(snap_path.read_text())
    except FileNotFoundError as exc:
        raise ConfigLoadError("snapshot.json ausente") from exc
    except json.JSONDecodeError as exc:
        raise ConfigLoadError(f"snapshot.json inválido: {exc}") from exc

    if manifest_raw.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise ConfigLoadError("schema_version divergente")

    declared: dict[str, str] = manifest_raw.get("parts", {})
    extras: dict[str, str] = manifest_raw.get("extras", {})

    for name, expected in {**declared, **extras}.items():
        p = config_dir / name
        try:
            content = p.read_bytes()
        except FileNotFoundError as exc:
            raise ConfigLoadError(f"parte declarada ausente: {name}") from exc
        if hashlib.sha256(content).hexdigest() != expected:
            raise ConfigLoadError(f"hash divergente em {name}")

    providers_doc = json.loads((config_dir / "providers.json").read_text())
    functions_doc = json.loads((config_dir / "functions.json").read_text())
    validate_providers(providers_doc)
    validate_functions(functions_doc)

    persona_dirs = {p.parent.name for p in config_dir.glob("personas/*/*")}
    for fname, spec in functions_doc.items():
        if spec["specialist"] not in persona_dirs:
            raise ConfigLoadError(
                f"{fname}: especialista '{spec['specialist']}' sem bundle em personas/")

    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "providers": providers_doc,
        "functions": functions_doc,
        "manifest": {"parts": declared, "extras": extras},
    }


# ------------------------------------------------------- eligibility helper

def provider_eligible(spec: dict[str, Any], inventory_entry: dict[str, Any] | None,
                      *, aegis_allows: bool = True,
                      capability_ok: bool = True) -> tuple[bool, str | None]:
    """Elegibilidade canônica:
    enabled AND approved AND healthy AND capability AND Aegis.

    `discovered` é informação observada, NÃO autorização — não participa.
    Falta de health falha fechada como PROVIDER_UNHEALTHY.
    """
    if not spec.get("enabled"):
        return False, "PROVIDER_DISABLED"
    if not spec.get("approved"):
        return False, "PROVIDER_NOT_APPROVED"
    obs = inventory_entry or {}
    if not obs.get("healthy"):
        return False, "PROVIDER_UNHEALTHY"
    if not capability_ok:
        return False, "PROVIDER_CAPABILITY_MISMATCH"
    if not aegis_allows:
        return False, "AEGIS_DENIED"
    return True, None


def load_observed(cache_dir: Path) -> list[dict[str, Any]]:
    """Observado lido separadamente, sanitizado; ausência = lista vazia."""
    inv = cache_dir / "inventory.json"
    try:
        doc = json.loads(inv.read_text())
        validate_inventory(doc)
        return doc.get("entries", [])
    except FileNotFoundError:
        return []
