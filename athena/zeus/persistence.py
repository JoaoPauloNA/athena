"""CFG-2: persistência do registro do Zeus em disco (sob ~/.athena/).

Formato: JSON versionado com hash — cada arquivo `zeus-registry/<version>.json`
é imutável; gravar nova versão cria novo arquivo. O registro sobrevive a
reinício recarregando a versão marcada como atual em `current.json`.
Hash de cada arquivo é conferido na carga (mesmo padrão do snapshot).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path

from .contracts import AgentRecord
from .registry import ZeusRegistry

REGISTRY_DIRNAME = "zeus-registry"
_CURRENT_FILE = "current.json"
_CURRENT_HASH_FILE = "current.json.sha256"
_VERSION_FILE_RE = re.compile(r"zeus\.registry\.v1\.0\.[1-9][0-9]*\.json")
_MAX_REGISTRY_BYTES = 1024 * 1024


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
    (out / _CURRENT_HASH_FILE).write_text(_hash_bytes(cb))
    return out


def _read_regular_at(directory_fd: int, name: str) -> bytes:
    file_fd = -1
    try:
        file_fd = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        with os.fdopen(file_fd, "rb") as stream:
            file_fd = -1
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ConfigLoadError("registro inválido")
            content = stream.read(_MAX_REGISTRY_BYTES + 1)
        if len(content) > _MAX_REGISTRY_BYTES:
            raise ConfigLoadError("registro inválido")
        return content
    except ConfigLoadError:
        raise
    except (FileNotFoundError, NotADirectoryError, OSError) as exc:
        raise ConfigLoadError("registro indisponível") from exc
    finally:
        if file_fd >= 0:
            os.close(file_fd)


def _parse_json(content: bytes) -> object:
    try:
        return json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ConfigLoadError("registro inválido") from exc


def _registry_material(base_dir: Path) -> tuple[dict[str, bytes], str]:
    regdir = base_dir / REGISTRY_DIRNAME
    directory_fd = -1
    try:
        directory_fd = os.open(
            regdir,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        names = os.listdir(directory_fd)
        version_names = sorted(name for name in names if _VERSION_FILE_RE.fullmatch(name))
        if not version_names:
            raise ConfigLoadError("registro vazio")
        required = [_CURRENT_FILE, _CURRENT_HASH_FILE]
        for name in version_names:
            required.extend((name, f"{name}.sha256"))
        material = {name: _read_regular_at(directory_fd, name) for name in required}
    except ConfigLoadError:
        raise
    except (FileNotFoundError, NotADirectoryError, OSError) as exc:
        raise ConfigLoadError("registro indisponível") from exc
    finally:
        if directory_fd >= 0:
            os.close(directory_fd)
    identity = _hash_bytes(
        b"".join(
            name.encode("utf-8") + b"\0" + material[name]
            for name in sorted(material)
        )
    )
    return material, identity


def _verified_payload(material: dict[str, bytes], name: str) -> object:
    expected = material[f"{name}.sha256"].decode("ascii", errors="strict").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ConfigLoadError("hash de registro inválido")
    raw = material[name]
    if _hash_bytes(raw) != expected:
        raise ConfigLoadError("hash divergente no registro")
    return _parse_json(raw)


def load_registry(base_dir: Path) -> ZeusRegistry:
    """Reconstruir o registro do disco com verificação de hash.

    Qualquer divergência de hash recusa a carga INTEIRA (padrão snapshot).
    """
    material, _identity = _registry_material(base_dir)
    meta = _verified_payload(material, _CURRENT_FILE)
    if not isinstance(meta, dict) or set(meta) != {
        "schema_version", "current", "transitions"
    } or meta.get("schema_version") != "athena.zeus.registry.v1":
        raise ConfigLoadError("metadados de registro inválidos")
    if not isinstance(meta.get("transitions"), list):
        raise ConfigLoadError("metadados de registro inválidos")

    versions_data: dict[str, dict[str, AgentRecord]] = {}

    entries = sorted(name for name in material if _VERSION_FILE_RE.fullmatch(name))
    for name in entries:
        agent_list = _verified_payload(material, name)
        if not isinstance(agent_list, list) or not agent_list:
            raise ConfigLoadError("versão de registro inválida")
        agents = {}
        for a in agent_list:
            if not isinstance(a, dict) or set(a) != {
                "agent_id", "persona_id", "registry_version", "capabilities",
                "runtime_class", "lifecycle", "prohibited_authorities",
            }:
                raise ConfigLoadError("entrada de registro inválida")
            try:
                rec = AgentRecord(
                    agent_id=a["agent_id"], persona_id=a["persona_id"],
                    registry_version=a["registry_version"],
                    capabilities=frozenset(a["capabilities"]),
                    runtime_class=a["runtime_class"],
                    lifecycle=a["lifecycle"],
                    prohibited_authorities=frozenset(a["prohibited_authorities"]),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ConfigLoadError("entrada de registro inválida") from exc
            if rec.agent_id in agents:
                raise ConfigLoadError("entrada de registro duplicada")
            agents[rec.agent_id] = rec
        versions_data[name.removesuffix(".json")] = agents

    reg = ZeusRegistry.__new__(ZeusRegistry)
    reg._versions = versions_data
    reg._transitions = list(meta["transitions"])
    cur = meta.get("current")
    if isinstance(cur, str) and cur in versions_data:
        reg._current = cur
    else:
        raise ConfigLoadError("versão atual do registro inválida")
    return reg


class ZeusRegistrySnapshotCache:
    """Cache apenas de registros já validados, por identidade de bytes."""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir
        self._identity: str | None = None
        self._current: ZeusRegistry | None = None

    def refresh(self) -> ZeusRegistry:
        _material, identity = _registry_material(self._base_dir)
        if identity == self._identity and self._current is not None:
            return self._current
        candidate = load_registry(self._base_dir)
        self._current = candidate
        self._identity = identity
        return candidate


class ConfigLoadError(Exception):
    """Recusa atômica da carga."""
