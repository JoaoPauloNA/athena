"""CFG-2: persistência do registro do Zeus em disco (sob ~/.athena/).

Formato: JSON versionado com hash — cada arquivo `zeus-registry/<version>.json`
é imutável; gravar nova versão cria novo arquivo. O registro sobrevive a
reinício recarregando a versão marcada como atual em `current.json`.
Hash de cada arquivo é conferido na carga (mesmo padrão do snapshot).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .contracts import AgentRecord
from .registry import ZeusRegistry

REGISTRY_DIRNAME = "zeus-registry"
_CURRENT_FILE = "current.json"


def _hash_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def save_registry(reg: ZeusRegistry, base_dir: Path) -> Path:
    """Persistir todas as versões + transições do registro.

    Idempotente por conteúdo: regravar a mesma versão produz bytes idênticos.
    """
    out = base_dir / REGISTRY_DIRNAME
    out.mkdir(parents=True, exist_ok=True)
    data = reg.export_all()
    for version, payload in data["versions"].items():
        b = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        fname = f"{version}.json"
        (out / fname).write_bytes(b)
        # hash ao lado para conferência de carga
        (out / f"{fname}.sha256").write_text(_hash_bytes(b))
    current = {"schema_version": "athena.zeus.registry.v1",
               "current": data["current"],
               "transitions": data["transitions"]}
    cb = json.dumps(current, ensure_ascii=False, sort_keys=True).encode()
    (out / _CURRENT_FILE).write_bytes(cb)
    return out


def load_registry(base_dir: Path) -> ZeusRegistry:
    """Reconstruir o registro do disco com verificação de hash.

    Qualquer divergência de hash recusa a carga INTEIRA (padrão snapshot).
    """
    regdir = base_dir / REGISTRY_DIRNAME
    current_path = regdir / _CURRENT_FILE
    try:
        meta = json.loads(current_path.read_text())
    except FileNotFoundError as exc:
        raise ConfigLoadError(f"registro do Zeus ausente: {current_path}") from exc

    versions_data: dict[str, dict[str, AgentRecord]] = {}

    entries = sorted(p for p in regdir.glob("*.json") if p.name != _CURRENT_FILE)
    for path in entries:
        sha_path = Path(str(path) + ".sha256")
        expected = sha_path.read_text().strip()
        raw = path.read_bytes()
        if _hash_bytes(raw) != expected:
            raise ConfigLoadError(f"hash divergente em {path.name} — carga recusada")
        agent_list = json.loads(raw.decode())  # formato: lista de agentes
        agents = {}
        for a in agent_list:
            rec = AgentRecord(
                agent_id=a["agent_id"], persona_id=a["persona_id"],
                registry_version=a.get("registry_version", ""),
                capabilities=frozenset(a["capabilities"]),
                runtime_class=a["runtime_class"],
                lifecycle=a["lifecycle"],
                prohibited_authorities=frozenset(a["prohibited_authorities"]),
            )
            agents[rec.agent_id] = rec
        versions_data[path.stem] = agents

    reg = ZeusRegistry.__new__(ZeusRegistry)
    reg._versions = versions_data
    reg._transitions = list(meta.get("transitions", []))
    cur = meta.get("current")
    if cur and cur in versions_data:
        reg._current = cur
    elif versions_data:
        reg._current = max(versions_data)  # fallback determinístico
    else:
        raise ConfigLoadError("registro sem nenhuma versão válida")
    return reg


class ConfigLoadError(Exception):
    """Recusa atômica da carga."""
