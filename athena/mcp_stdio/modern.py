"""Helpers for MCP protocol revision 2026-07-28 over stdio."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

MODERN_PROTOCOL_VERSION = "2026-07-28"
LEGACY_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "athena-mcp"
SERVER_VERSION = "0.2.0"
SUPPORTED_MODERN_VERSIONS: tuple[str, ...] = (MODERN_PROTOCOL_VERSION,)

_META_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion"
_META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
_META_CLIENT_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
_META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

_VERSION_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_META_PREFIX_LABEL_PATTERN = re.compile(r"^[A-Za-z](?:[A-Za-z0-9-]*[A-Za-z0-9])?$")
_META_NAME_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
_MAX_META_DEPTH = 8
_MAX_META_NODES = 128
_MAX_META_OBJECT_KEYS = 32
_MAX_META_LIST_LEN = 32
_MAX_STRING_LEN = 256
_MAX_REQUEST_ID_LEN = 128
_MAX_CANCEL_REASON_LEN = 256
MAX_INPUT_LINE_BYTES = 65_536
_DISCOVER_TTL_MS = 3_600_000
_LIST_TTL_MS = 300_000

KNOWN_NOTIFICATIONS = frozenset(
    {"notifications/initialized", "notifications/cancelled"}
)


class ModernMetaError(ValueError):
    """Invalid modern `_meta` payload."""


@dataclass
class _MetaStats:
    nodes: int = 0


def normalize_request_id(value: object) -> str | int | None:
    """Return a hashable JSON-RPC id or None for malformed values."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        if not value or len(value) > _MAX_REQUEST_ID_LEN:
            return None
        return value
    return None


def params_meta_kind(params: object) -> str:
    """Classify params as legacy, partial `_meta`, or modern."""
    if not isinstance(params, Mapping):
        return "legacy"
    if "_meta" not in params:
        return "legacy"
    meta = params.get("_meta")
    if not isinstance(meta, Mapping):
        return "partial"
    if _META_PROTOCOL_VERSION in meta:
        return "modern"
    return "partial"


def has_modern_meta(params: object) -> bool:
    return params_meta_kind(params) == "modern"


def _bounded_string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > _MAX_STRING_LEN:
        raise ModernMetaError(f"{field} must be a non-empty string up to {_MAX_STRING_LEN} chars")
    return value


def _valid_meta_key(key: str) -> bool:
    if "/" not in key:
        return _META_NAME_PATTERN.fullmatch(key) is not None
    if key.count("/") != 1:
        return False
    prefix, name = key.split("/", 1)
    labels = prefix.split(".")
    if not labels or any(
        _META_PREFIX_LABEL_PATTERN.fullmatch(label) is None for label in labels
    ):
        return False
    return not name or _META_NAME_PATTERN.fullmatch(name) is not None


def _validate_json_tree(value: object, *, depth: int, stats: _MetaStats) -> None:
    stats.nodes += 1
    if stats.nodes > _MAX_META_NODES:
        raise ModernMetaError("metadata exceeds node limit")
    if value is None:
        return
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ModernMetaError("metadata number must be finite")
        return
    if isinstance(value, str):
        if len(value) > _MAX_STRING_LEN:
            raise ModernMetaError("metadata string exceeds length limit")
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) > _MAX_META_LIST_LEN:
            raise ModernMetaError("metadata array exceeds length limit")
        if depth >= _MAX_META_DEPTH:
            raise ModernMetaError("metadata exceeds nesting depth")
        for item in value:
            _validate_json_tree(item, depth=depth + 1, stats=stats)
        return
    if isinstance(value, Mapping):
        if depth >= _MAX_META_DEPTH:
            raise ModernMetaError("metadata exceeds nesting depth")
        if len(value) > _MAX_META_OBJECT_KEYS:
            raise ModernMetaError("metadata object exceeds key limit")
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > _MAX_STRING_LEN:
                raise ModernMetaError("metadata keys must be non-empty strings")
            _validate_json_tree(item, depth=depth + 1, stats=stats)
        return
    raise ModernMetaError("metadata contains unsupported value type")


def _validate_client_info(value: object) -> None:
    if not isinstance(value, Mapping):
        raise ModernMetaError("clientInfo must be an object")
    _bounded_string(value.get("name"), field="clientInfo.name")
    _bounded_string(value.get("version"), field="clientInfo.version")


def validate_modern_meta(meta: object) -> str:
    """Validate bounded modern `_meta` and return the requested protocol version."""
    stats = _MetaStats()
    _validate_json_tree(meta, depth=0, stats=stats)
    if not isinstance(meta, Mapping):
        raise ModernMetaError("_meta must be an object")
    if any(not isinstance(key, str) or not _valid_meta_key(key) for key in meta):
        raise ModernMetaError("metadata key has invalid syntax")
    if _META_PROTOCOL_VERSION not in meta:
        raise ModernMetaError("missing protocolVersion")
    if _META_CLIENT_CAPABILITIES not in meta:
        raise ModernMetaError("missing clientCapabilities")
    protocol_version = _bounded_string(
        meta[_META_PROTOCOL_VERSION],
        field="protocolVersion",
    )
    if _VERSION_PATTERN.fullmatch(protocol_version) is None:
        raise ModernMetaError("protocolVersion must use YYYY-MM-DD format")
    if _META_CLIENT_INFO in meta:
        _validate_client_info(meta[_META_CLIENT_INFO])
    if not isinstance(meta[_META_CLIENT_CAPABILITIES], Mapping):
        raise ModernMetaError("clientCapabilities must be an object")
    return protocol_version


def unsupported_version_error(requested: str) -> dict[str, object]:
    return {
        "code": -32022,
        "message": "Unsupported protocol version",
        "data": {
            "supported": list(SUPPORTED_MODERN_VERSIONS),
            "requested": requested,
        },
    }


def server_info_meta() -> dict[str, object]:
    return {_META_SERVER_INFO: {"name": SERVER_NAME, "version": SERVER_VERSION}}


def wrap_modern_success_result(result: Mapping[str, Any] | None = None) -> dict[str, object]:
    payload: dict[str, object] = dict(result or {})
    result_meta = payload.get("_meta")
    merged_meta = dict(result_meta) if isinstance(result_meta, Mapping) else {}
    merged_meta.update(server_info_meta())
    payload["_meta"] = merged_meta
    payload["resultType"] = "complete"
    return payload


def discover_result() -> dict[str, object]:
    return wrap_modern_success_result(
        {
            "supportedVersions": list(SUPPORTED_MODERN_VERSIONS),
            "capabilities": {"tools": {}},
            "ttlMs": _DISCOVER_TTL_MS,
            "cacheScope": "public",
        }
    )


def wrap_modern_list_result(tools: list[Mapping[str, Any]]) -> dict[str, object]:
    return wrap_modern_success_result(
        {
            "tools": tools,
            "ttlMs": _LIST_TTL_MS,
            "cacheScope": "public",
        }
    )


def wrap_modern_call_result(result: Mapping[str, Any]) -> dict[str, object]:
    return wrap_modern_success_result(result)
