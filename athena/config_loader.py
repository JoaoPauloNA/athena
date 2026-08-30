"""Secure, atomic loading of Athena's desired configuration snapshot.

Observed inventory is deliberately handled separately and never authorizes work.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import unicodedata
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any
from urllib.parse import parse_qsl, urlsplit

CONFIG_SCHEMA_VERSION = "athena.config.v1.1"

ALLOWED_MODES = ("agent_cli", "api", "local", "remote")
ALLOWED_RUNTIME_CLASSES = ("local", "frontier")

MAX_SNAPSHOT_BYTES = 256 * 1024
MAX_PART_BYTES = 1024 * 1024
MAX_JSON_DEPTH = 32
MAX_JSON_ITEMS = 10_000
MAX_DECLARED_PARTS = 256

_PART_FILES = ("providers.json", "functions.json")
_SNAPSHOT_FILE = "snapshot.json"
_TEMP_PREFIX = ".snapshot.json."
_TEMP_SUFFIX = ".tmp"
_HASH_RE = re.compile(r"[0-9a-f]{64}")
_ID_RE = re.compile(r"[a-z0-9-]+")
_VERSION_RE = re.compile(r"[a-z0-9][a-z0-9._-]*")
_PROVIDER_KEYS = {
    "approved",
    "base_url",
    "command",
    "default_model",
    "enabled",
    "mode",
    "runtime_class",
    "secret_ref",
}
_FUNCTION_KEYS = {"min_status", "specialist", "version"}
_NORMALIZED_SECRET_KEYS = {
    "apikey",
    "authorization",
    "bearertoken",
    "clientsecret",
    "password",
    "secret",
    "token",
}


class ConfigLoadError(Exception):
    """The candidate snapshot is invalid and must not become current."""


class _DuplicateKeyError(ValueError):
    pass


def _fail(msg: str) -> None:
    raise ValueError(f"config inválida: {msg}")


def _normalized_key(key: str) -> str:
    normalized = unicodedata.normalize("NFKC", key).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _is_secret_value_key(key: str) -> bool:
    normalized = _normalized_key(key)
    if normalized == "secretref":
        return False
    return (
        normalized in _NORMALIZED_SECRET_KEYS
        or "secret" in normalized
        or "password" in normalized
        or normalized.endswith(("apikey", "token", "authorization"))
    )


def _valid_secret_ref(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(
        r"[a-z][a-z0-9+.-]*:[^\s:][^\s]*", value
    ) is not None


def _reject_secret_values(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str) and _is_secret_value_key(key):
                _fail("campo de segredo proibido; use 'secret_ref'")
            if (
                isinstance(key, str)
                and _normalized_key(key) == "secretref"
                and (key != "secret_ref" or not _valid_secret_ref(child))
            ):
                _fail("secret_ref inválido")
            _reject_secret_values(child)
    elif isinstance(value, list):
        for child in value:
            _reject_secret_values(child)


# ------------------------------------------------------------------ desired

def validate_provider_spec(pid: str, spec: dict[str, Any]) -> None:
    if not isinstance(pid, str) or not _ID_RE.fullmatch(pid):
        _fail("provider_id inválido")
    if not isinstance(spec, dict):
        _fail("especificação de provider deve ser objeto")

    _reject_secret_values(spec)
    if set(spec) - _PROVIDER_KEYS:
        _fail(f"{pid}: campo de provider não suportado")
    mode = spec.get("mode")
    if not isinstance(mode, str) or mode not in ALLOWED_MODES:
        _fail(f"{pid}: mode deve ser um de {ALLOWED_MODES}")

    runtime_class = spec.get("runtime_class")
    if not isinstance(runtime_class, str) or runtime_class not in ALLOWED_RUNTIME_CLASSES:
        _fail(f"{pid}: runtime_class deve ser local|frontier")

    for flag in ("enabled", "approved"):
        if type(spec.get(flag)) is not bool:
            _fail(f"{pid}: '{flag}' booleano obrigatório")

    secret_ref = spec.get("secret_ref")
    if secret_ref is not None and not _valid_secret_ref(secret_ref):
        _fail(f"{pid}: secret_ref deve ser 'scheme:item'")

    base_url = spec.get("base_url")
    if base_url is not None:
        if not isinstance(base_url, str):
            _fail(f"{pid}: base_url deve ser string")
        try:
            parsed_url = urlsplit(base_url)
            has_userinfo = parsed_url.username is not None or parsed_url.password is not None
            has_secret_query = any(
                _is_secret_value_key(key)
                for key, _value in parse_qsl(parsed_url.query, keep_blank_values=True)
            )
            valid_http_url = (
                parsed_url.scheme in ("http", "https")
                and parsed_url.hostname is not None
                and not any(character.isspace() for character in base_url)
            )
        except ValueError:
            has_userinfo = True
            has_secret_query = True
            valid_http_url = False
        if has_userinfo or has_secret_query:
            _fail(f"{pid}: base_url não pode conter credenciais")
        if mode in ("api", "local") and not valid_http_url:
            _fail(f"{pid}: base_url inválida")

    command = spec.get("command")
    if command is not None and not isinstance(command, str):
        _fail(f"{pid}: command deve ser string")
    if mode == "agent_cli" and (not isinstance(command, str) or not command.strip()):
        _fail(f"{pid}: agent_cli exige 'command'")

    default_model = spec.get("default_model")
    if default_model is not None and (
        not isinstance(default_model, str) or not default_model.strip()
    ):
        _fail(f"{pid}: default_model deve ser string não vazia")


def validate_providers(doc: dict[str, Any]) -> None:
    if not isinstance(doc, dict) or not doc:
        _fail("providers.json deve ser objeto não vazio")
    _reject_secret_values(doc)
    for pid, spec in doc.items():
        validate_provider_spec(pid, spec)


def validate_functions(doc: dict[str, Any]) -> None:
    if not isinstance(doc, dict):
        _fail("functions.json deve ser objeto")
    _reject_secret_values(doc)
    for fname, spec in doc.items():
        if not isinstance(fname, str) or not _ID_RE.fullmatch(fname):
            _fail("function_id inválido")
        if not isinstance(spec, dict):
            _fail(f"{fname}: especificação deve ser objeto")
        if set(spec) - _FUNCTION_KEYS:
            _fail(f"{fname}: campo de função não suportado")
        specialist = spec.get("specialist")
        version = spec.get("version")
        if not isinstance(specialist, str) or not _ID_RE.fullmatch(specialist):
            _fail(f"{fname}: exige campo 'specialist' canônico")
        if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
            _fail(f"{fname}: exige campo 'version' canônico")
        min_status = spec.get("min_status")
        if min_status is not None and (
            not isinstance(min_status, str)
            or min_status not in ("approved", "candidate")
        ):
            _fail(f"{fname}: min_status só approved|candidate")


# ----------------------------------------------------------------- observed

_OBSERVED_KEYS = ("discovered", "healthy")


def validate_inventory(doc: dict[str, Any]) -> None:
    """Validate disposable observed state independently from desired state."""
    if not isinstance(doc, dict):
        _fail("cache/inventory.json deve ser objeto")

    def _reject_admin(obj: dict[str, Any]) -> None:
        if set(obj) & {"enabled", "approved"}:
            _fail("observado não pode carregar estado administrativo (desejado)")

    _reject_admin(doc)
    entries = doc.get("entries", [])
    if not isinstance(entries, list):
        _fail("inventory: entries deve ser lista")
    for entry in entries:
        if not isinstance(entry, dict):
            _fail("inventory: entry deve ser objeto")
        _reject_admin(entry)
        for key in ("cli_id", "provider_id"):
            value = entry.get(key)
            if value is not None and not isinstance(value, str):
                _fail(f"inventory: {key} deve ser string")
        for key in _OBSERVED_KEYS:
            if key in entry and type(entry[key]) is not bool:
                _fail(f"inventory: '{key}' booleano")


# ------------------------------------------------------------- secure JSON

def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError
        result[key] = value
    return result


def _validate_json_bounds(value: Any, *, depth: int = 1) -> int:
    if depth > MAX_JSON_DEPTH:
        raise ConfigLoadError("JSON excede profundidade máxima")
    count = 1
    if isinstance(value, dict):
        for child in value.values():
            count += _validate_json_bounds(child, depth=depth + 1)
            if count > MAX_JSON_ITEMS:
                raise ConfigLoadError("JSON excede contagem máxima")
    elif isinstance(value, list):
        for child in value:
            count += _validate_json_bounds(child, depth=depth + 1)
            if count > MAX_JSON_ITEMS:
                raise ConfigLoadError("JSON excede contagem máxima")
    return count


def _parse_json_bytes(content: bytes, label: str) -> Any:
    try:
        text = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ConfigLoadError(f"{label}: UTF-8 inválido") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except _DuplicateKeyError as exc:
        raise ConfigLoadError(f"{label}: chave JSON duplicada") from exc
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ConfigLoadError(f"{label}: JSON inválido") from exc
    _validate_json_bounds(value)
    return value


# ------------------------------------------------------------- safe paths

def _canonical_relative_path(name: Any) -> str:
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise ConfigLoadError("caminho de parte não canônico")
    if unicodedata.normalize("NFC", name) != name:
        raise ConfigLoadError("caminho de parte não canônico")
    path = PurePosixPath(name)
    if path.is_absolute() or str(path) != name:
        raise ConfigLoadError("caminho de parte não canônico")
    if any(part in ("", ".", "..") for part in path.parts):
        raise ConfigLoadError("caminho de parte não canônico")
    return name


def _validate_no_path_collisions(names: list[str]) -> None:
    normalized: set[str] = set()
    for raw_name in names:
        name = _canonical_relative_path(raw_name)
        collision_key = unicodedata.normalize("NFC", name).casefold()
        if collision_key in normalized:
            raise ConfigLoadError("caminhos de partes colidem")
        normalized.add(collision_key)


def _read_regular_file_at(root_fd: int, relative_name: str, max_bytes: int) -> bytes:
    name = _canonical_relative_path(relative_name)
    directory_fd = -1
    file_fd = -1
    try:
        directory_fd = os.dup(root_fd)
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        components = PurePosixPath(name).parts
        for component in components[:-1]:
            next_fd = os.open(
                component,
                directory_flags | no_follow,
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(
            components[-1],
            os.O_RDONLY | no_follow,
            dir_fd=directory_fd,
        )
        with os.fdopen(file_fd, "rb") as stream:
            file_fd = -1
            mode = os.fstat(stream.fileno()).st_mode
            if not stat.S_ISREG(mode):
                raise ConfigLoadError(f"{name}: alvo não é arquivo regular")
            content = stream.read(max_bytes + 1)
    except ConfigLoadError:
        raise
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise ConfigLoadError(f"{name}: parte declarada ausente") from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise ConfigLoadError(f"{name}: parte inacessível") from exc
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        if directory_fd >= 0:
            os.close(directory_fd)
    if len(content) > max_bytes:
        raise ConfigLoadError(f"{name}: tamanho máximo excedido")
    return content


def _read_regular_file(root: Path, relative_name: str, max_bytes: int) -> bytes:
    directory_fd = -1
    try:
        resolved_root = root.resolve(strict=True)
        directory_fd = os.open(
            resolved_root,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        return _read_regular_file_at(directory_fd, relative_name, max_bytes)
    except ConfigLoadError:
        raise
    except (FileNotFoundError, NotADirectoryError) as exc:
        name = _canonical_relative_path(relative_name)
        raise ConfigLoadError(f"{name}: parte declarada ausente") from exc
    except (OSError, RuntimeError, ValueError) as exc:
        name = _canonical_relative_path(relative_name)
        raise ConfigLoadError(f"{name}: parte inacessível") from exc
    finally:
        if directory_fd >= 0:
            os.close(directory_fd)


def _persona_path(specialist: str, version: str) -> str:
    return f"personas/{specialist}/{version}/bundle.json"


def _validate_hash(value: Any) -> str:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ConfigLoadError("hash de parte inválido")
    return value


def _validate_manifest(value: Any) -> tuple[dict[str, str], dict[str, str]]:
    if not isinstance(value, dict) or set(value) != {"schema_version", "parts", "extras"}:
        raise ConfigLoadError("schema do manifesto inválido")
    if value["schema_version"] != CONFIG_SCHEMA_VERSION:
        raise ConfigLoadError("schema_version divergente")
    parts = value["parts"]
    extras = value["extras"]
    if not isinstance(parts, dict) or not isinstance(extras, dict):
        raise ConfigLoadError("coleções de partes inválidas")
    if set(parts) != set(_PART_FILES):
        raise ConfigLoadError("partes obrigatórias divergentes")
    if len(parts) + len(extras) > MAX_DECLARED_PARTS:
        raise ConfigLoadError("quantidade de partes excedida")
    names = [*parts, *extras]
    _validate_no_path_collisions(names)
    for name in extras:
        path = PurePosixPath(name)
        if (
            len(path.parts) != 4
            or path.parts[0] != "personas"
            or not _ID_RE.fullmatch(path.parts[1])
            or not _VERSION_RE.fullmatch(path.parts[2])
            or path.parts[3] != "bundle.json"
        ):
            raise ConfigLoadError("parte extra não canônica")
    return (
        {name: _validate_hash(expected) for name, expected in parts.items()},
        {name: _validate_hash(expected) for name, expected in extras.items()},
    )


# ------------------------------------------------------------- snapshot

def part_hash(path: Path) -> str:
    """Compatibility helper for manifest construction."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_manifest(
    config_dir: Path,
    extra_parts: dict[str, Path] | None = None,
) -> dict[str, Any]:
    """Build a deterministic manifest over required parts and persona bundles."""
    parts = {
        name: hashlib.sha256(
            _read_regular_file(config_dir, name, MAX_PART_BYTES)
        ).hexdigest()
        for name in _PART_FILES
    }
    if extra_parts is None:
        discovered = sorted(
            path.relative_to(config_dir).as_posix()
            for path in config_dir.glob("personas/*/*/bundle.json")
        )
        extra_names = discovered
    else:
        extra_names = sorted(extra_parts)
        for name, supplied_path in extra_parts.items():
            canonical = _canonical_relative_path(name)
            expected_path = (config_dir / canonical).resolve(strict=False)
            if supplied_path.resolve(strict=False) != expected_path:
                raise ConfigLoadError("parte extra não corresponde ao caminho declarado")
    extras = {
        name: hashlib.sha256(
            _read_regular_file(config_dir, name, MAX_PART_BYTES)
        ).hexdigest()
        for name in extra_names
    }
    _validate_manifest(
        {"schema_version": CONFIG_SCHEMA_VERSION, "parts": parts, "extras": extras}
    )
    return {"schema_version": CONFIG_SCHEMA_VERSION, "parts": parts, "extras": extras}


def write_snapshot(config_dir: Path, manifest: dict[str, Any]) -> None:
    """Validate and durably publish ``snapshot.json`` in one directory.

    Once the directory descriptor is acquired, validation, temporary-file creation,
    replacement, cleanup, and directory fsync are anchored to that descriptor.  The
    portable POSIX residual is substitution of an ancestor directory immediately
    before ``os.open(config_dir)`` acquires the descriptor; no path lookup is trusted
    after that point.  A cooperating external writer must still publish new part files
    before calling this function; POSIX has no transaction spanning independent files,
    so hostile in-place mutation after the final validation remains outside this API.
    """
    _validate_manifest(manifest)
    content = (
        json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    if len(content) > MAX_SNAPSHOT_BYTES:
        raise ConfigLoadError("snapshot.json: tamanho máximo excedido")

    directory_fd = -1
    file_fd = -1
    temp_name: str | None = None
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        if config_dir.is_symlink():
            raise ConfigLoadError("diretório de configuração não pode ser symlink")
        directory_fd = os.open(
            config_dir,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )

        for _attempt in range(100):
            candidate_name = f"{_TEMP_PREFIX}{secrets.token_hex(16)}{_TEMP_SUFFIX}"
            try:
                file_fd = os.open(
                    candidate_name,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=directory_fd,
                )
            except FileExistsError:
                continue
            temp_name = candidate_name
            break
        if file_fd < 0 or temp_name is None:
            raise ConfigLoadError("não foi possível criar temporário único")

        os.fchmod(file_fd, 0o600)
        stream = os.fdopen(file_fd, "wb")
        file_fd = -1
        with stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())

        # Validate the exact serialized bytes at the last possible pre-replace point.
        _load_from_snapshot_bytes(config_dir, content, root_fd=directory_fd)
        os.replace(
            temp_name,
            _SNAPSHOT_FILE,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        temp_name = None
        os.fsync(directory_fd)
    except ConfigLoadError:
        raise
    except (OSError, ValueError) as exc:
        raise ConfigLoadError("falha ao publicar snapshot atômico") from exc
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        if temp_name is not None and directory_fd >= 0:
            try:
                os.unlink(temp_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
            except OSError:
                pass
        if directory_fd >= 0:
            os.close(directory_fd)


def _load_from_snapshot_bytes(
    config_dir: Path,
    snapshot_bytes: bytes,
    *,
    root_fd: int | None = None,
) -> dict[str, Any]:
    manifest_value = _parse_json_bytes(snapshot_bytes, _SNAPSHOT_FILE)
    declared, extras = _validate_manifest(manifest_value)

    parsed: dict[str, Any] = {}
    for name, expected in {**declared, **extras}.items():
        if root_fd is None:
            content = _read_regular_file(config_dir, name, MAX_PART_BYTES)
        else:
            content = _read_regular_file_at(root_fd, name, MAX_PART_BYTES)
        if hashlib.sha256(content).hexdigest() != expected:
            raise ConfigLoadError(f"hash divergente em {name}")
        parsed[name] = _parse_json_bytes(content, name)

    providers_doc = parsed["providers.json"]
    functions_doc = parsed["functions.json"]
    try:
        validate_providers(providers_doc)
        validate_functions(functions_doc)
    except ValueError as exc:
        raise ConfigLoadError(str(exc)) from exc

    referenced = {
        _persona_path(spec["specialist"], spec["version"])
        for spec in functions_doc.values()
    }
    if set(extras) != referenced:
        raise ConfigLoadError("bundles de persona declarados divergem das funções")
    for name in sorted(referenced):
        bundle = parsed[name]
        path = PurePosixPath(name)
        if not isinstance(bundle, dict):
            raise ConfigLoadError(f"{name}: bundle deve ser objeto")
        _reject_secret_values_as_load_error(bundle, name)
        if bundle.get("specialist_id") != path.parts[1]:
            raise ConfigLoadError(f"{name}: specialist_id divergente")
        if bundle.get("version") != path.parts[2]:
            raise ConfigLoadError(f"{name}: version divergente")

    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "providers": providers_doc,
        "functions": functions_doc,
        "personas": {name: parsed[name] for name in sorted(referenced)},
        "manifest": {"parts": declared, "extras": extras},
    }


def _reject_secret_values_as_load_error(value: Any, label: str) -> None:
    try:
        _reject_secret_values(value)
    except ValueError as exc:
        raise ConfigLoadError(f"{label}: campo de segredo proibido") from exc


def load_config(config_dir: Path) -> dict[str, Any]:
    """One-shot compatibility wrapper using the secure same-byte load path."""
    snapshot_bytes = _read_regular_file(config_dir, _SNAPSHOT_FILE, MAX_SNAPSHOT_BYTES)
    return _load_from_snapshot_bytes(config_dir, snapshot_bytes)


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(child) for child in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw(child) for child in value]
    return value


class ConfigSnapshotCache:
    """Explicit cache that installs only complete, deeply isolated snapshots."""

    def __init__(self, config_dir: Path) -> None:
        self._config_dir = config_dir
        self._identity: str | None = None
        self._current: Any | None = None

    @property
    def current(self) -> dict[str, Any] | None:
        if self._current is None:
            return None
        return _thaw(self._current)

    def refresh(self) -> dict[str, Any]:
        snapshot_bytes = _read_regular_file(
            self._config_dir,
            _SNAPSHOT_FILE,
            MAX_SNAPSHOT_BYTES,
        )
        identity = hashlib.sha256(snapshot_bytes).hexdigest()
        candidate = _load_from_snapshot_bytes(self._config_dir, snapshot_bytes)
        if identity == self._identity and self._current is not None:
            return _thaw(self._current)

        frozen_candidate = _freeze(candidate)
        self._current = frozen_candidate
        self._identity = identity
        return _thaw(frozen_candidate)


# ------------------------------------------------------- eligibility helper

def provider_eligible(
    spec: dict[str, Any],
    inventory_entry: dict[str, Any] | None,
    *,
    aegis_allows: bool = True,
    capability_ok: bool = True,
) -> tuple[bool, str | None]:
    """Apply desired + observed eligibility; discovery alone never authorizes."""
    if not spec.get("enabled"):
        return False, "PROVIDER_DISABLED"
    if not spec.get("approved"):
        return False, "PROVIDER_NOT_APPROVED"
    observed = inventory_entry or {}
    if not observed.get("healthy"):
        return False, "PROVIDER_UNHEALTHY"
    if not capability_ok:
        return False, "PROVIDER_CAPABILITY_MISMATCH"
    if not aegis_allows:
        return False, "AEGIS_DENIED"
    return True, None


def load_observed(cache_dir: Path) -> list[dict[str, Any]]:
    """Read observed inventory separately; absence means no observations."""
    inventory = cache_dir / "inventory.json"
    try:
        content = inventory.read_bytes()
    except FileNotFoundError:
        return []
    if len(content) > MAX_PART_BYTES:
        _fail("cache/inventory.json excede tamanho máximo")
    try:
        document = _parse_json_bytes(content, "cache/inventory.json")
    except ConfigLoadError as exc:
        _fail(str(exc))
    validate_inventory(document)
    return document.get("entries", [])
