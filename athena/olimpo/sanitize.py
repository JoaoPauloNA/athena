"""Parsing JSON limitado e projeções sanitizadas para O-0."""

from __future__ import annotations

import json
import re
from dataclasses import fields, is_dataclass
from typing import Any

from .contracts import (
    CAPABILITY_UNAVAILABLE,
    FORBIDDEN_RESPONSE_KEYS,
    MAX_JSON_DEPTH,
    MAX_JSON_ITEMS,
    MAX_LIST_ITEMS,
    MAX_RESPONSE_BYTES,
    STABLE_REASON_CODES,
    OlimpoError,
    validate_identifier,
)

_DUPLICATE_KEY = object()
_SECRET_HINT = re.compile(
    r"(?:bearer|credential|password|secret|token|api[_-]?key)", re.IGNORECASE
)
_REASON_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_URL_CREDENTIAL = re.compile(r"://[^/@]+@[^/\s]+")
_HOME_PATH = re.compile(r"(?:/Users/|/home/|~)[^\s\"']+")


class _DuplicateKeyError(ValueError):
    pass


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(key)
        result[key] = value
    return result


def _validate_json_bounds(value: Any, *, depth: int = 1) -> int:
    if depth > MAX_JSON_DEPTH:
        raise OlimpoError("OLIMPO_JSON_INVALID")
    count = 1
    if isinstance(value, dict):
        for child in value.values():
            count += _validate_json_bounds(child, depth=depth + 1)
    elif isinstance(value, list):
        for child in value:
            count += _validate_json_bounds(child, depth=depth + 1)
    if count > MAX_JSON_ITEMS:
        raise OlimpoError("OLIMPO_JSON_INVALID")
    return count


def parse_json_object(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_JSON_DEPTH * 1024:
        raise OlimpoError("OLIMPO_REQUEST_TOO_LARGE")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OlimpoError("OLIMPO_JSON_INVALID") from exc
    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
        )
    except _DuplicateKeyError as exc:
        raise OlimpoError("OLIMPO_JSON_DUPLICATE_KEY") from exc
    except json.JSONDecodeError as exc:
        raise OlimpoError("OLIMPO_JSON_INVALID") from exc
    if not isinstance(parsed, dict):
        raise OlimpoError("OLIMPO_JSON_INVALID")
    _validate_json_bounds(parsed)
    return parsed


def redact_string(value: str) -> str:
    cleaned = _URL_CREDENTIAL.sub("://[redacted]@", value)
    cleaned = _HOME_PATH.sub("[path-redacted]", cleaned)
    if _SECRET_HINT.search(cleaned):
        return "[redacted]"
    return cleaned


def sanitize_scalar(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, str):
        return redact_string(value)
    raise OlimpoError("OLIMPO_INTERNAL_ERROR")


def sanitize_mapping(
    value: object,
    *,
    allowed_keys: frozenset[str],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OlimpoError("OLIMPO_INTERNAL_ERROR")
    sanitized: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise OlimpoError("OLIMPO_INTERNAL_ERROR")
        lowered = key.casefold()
        if key in FORBIDDEN_RESPONSE_KEYS or lowered in FORBIDDEN_RESPONSE_KEYS:
            continue
        if key not in allowed_keys:
            continue
        if isinstance(item, dict):
            continue
        if isinstance(item, (list, tuple)):
            sanitized[key] = [
                sanitize_scalar(entry)
                for entry in item
                if isinstance(entry, (str, int, float, bool)) or entry is None
            ]
        else:
            sanitized[key] = sanitize_scalar(item)
    return sanitized


def project_task(record: object) -> dict[str, Any]:
    if record is None:
        raise OlimpoError("OLIMPO_TASK_NOT_FOUND", status=404)
    allowed = frozenset(
        {
            "task_handle",
            "task_type",
            "state",
            "priority",
            "revision",
            "created_at",
            "updated_at",
            "execution_id",
            "execution_status",
            "validation_status",
            "delivery_status",
            "chronos_action",
            "attempts_used",
            "reason_codes",
        }
    )
    source = _entry_mapping(record)
    if source is None:
        raise OlimpoError("OLIMPO_INTERNAL_ERROR", status=500)
    source = {key: value for key, value in source.items() if key in allowed}
    handle = source.get("task_handle")
    if isinstance(handle, str):
        validate_identifier(handle, field_name="task_handle")
    raw_codes = source.pop("reason_codes", None)
    payload = sanitize_mapping(source, allowed_keys=allowed)
    if isinstance(handle, str):
        payload["task_handle"] = handle
    payload["found"] = True
    if isinstance(raw_codes, (list, tuple)):
        codes = [entry for entry in raw_codes if isinstance(entry, str)]
        for code in codes:
            if not _REASON_CODE_RE.fullmatch(code):
                raise OlimpoError("OLIMPO_INTERNAL_ERROR", status=500)
        payload["reason_codes"] = codes
    return payload


def project_execution(entry: object) -> dict[str, Any]:
    if entry is None:
        raise OlimpoError("OLIMPO_EXECUTION_NOT_FOUND", status=404)
    allowed = frozenset(
        {
            "execution_id",
            "request_id",
            "tool",
            "state",
            "attempts",
            "current_attempt_id",
            "finalized",
            "found",
            "requested",
        }
    )
    attempt_fields = frozenset(
        {
            "absolute_deadline_s",
            "attempt_id",
            "duration_ms",
            "exit_code",
            "finished_at",
            "idle_deadline_s",
            "profile",
            "provider",
            "reason",
            "started_at",
            "state",
            "transport",
        }
    )
    if isinstance(entry, dict):
        source = dict(entry)
    elif hasattr(entry, "__dict__"):
        source = dict(entry.__dict__)
    else:
        raise OlimpoError("OLIMPO_INTERNAL_ERROR", status=500)
    payload = sanitize_mapping(source, allowed_keys=allowed)
    attempts = source.get("attempts")
    if isinstance(attempts, list):
        payload["attempts"] = [
            sanitize_mapping(item, allowed_keys=attempt_fields)
            for item in attempts
            if isinstance(item, dict)
        ]
    execution_id = source.get("execution_id")
    if isinstance(execution_id, str):
        payload["execution_id"] = execution_id
    payload["found"] = True
    return payload


def project_clio_status(snapshot: object) -> dict[str, Any]:
    if hasattr(snapshot, "level"):
        level = snapshot.level
        storage = snapshot.storage
        counters = snapshot.counters
    elif isinstance(snapshot, dict):
        level = snapshot.get("level")
        storage = snapshot.get("storage")
        counters = snapshot.get("counters")
    else:
        raise OlimpoError("OLIMPO_INTERNAL_ERROR", status=500)
    if not isinstance(level, str) or not isinstance(storage, str):
        raise OlimpoError("OLIMPO_INTERNAL_ERROR", status=500)
    counter_payload: dict[str, int] = {}
    if isinstance(counters, dict):
        for key, value in counters.items():
            if isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool):
                counter_payload[key] = value
    return {
        "schema_version": "olimpo.clio-status.v0",
        "level": redact_string(level),
        "storage": redact_string(storage),
        "counters": counter_payload,
    }


def _entry_mapping(entry: object) -> dict[str, Any] | None:
    if isinstance(entry, dict):
        return dict(entry)
    if is_dataclass(entry):
        return {
            field.name: getattr(entry, field.name)
            for field in fields(entry)
            if getattr(entry, field.name) is not None
        }
    if hasattr(entry, "__dict__"):
        return {
            key: value
            for key, value in entry.__dict__.items()
            if value is not None
        }
    return None


def project_inventory(entries: list[object]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for entry in entries[:MAX_LIST_ITEMS]:
        raw = _entry_mapping(entry)
        if raw is None:
            continue
        item = sanitize_mapping(
            raw,
            allowed_keys=frozenset(
                {
                    "provider_id",
                    "function_id",
                    "mode",
                    "runtime_class",
                    "enabled",
                    "approved",
                    "default_model",
                    "specialist",
                    "version",
                    "min_status",
                    "observed_discovered",
                    "observed_healthy",
                    "availability",
                }
            ),
        )
        if "availability" not in item:
            item["availability"] = CAPABILITY_UNAVAILABLE
        items.append(item)
    return {"schema_version": "olimpo.inventory.v0", "items": items}


def project_config_status(status: object) -> dict[str, Any]:
    if hasattr(status, "available"):
        available = status.available
        current_hash = getattr(status, "current_hash", None)
        schema_version = getattr(status, "schema_version", None)
    elif isinstance(status, dict):
        available = status.get("available")
        current_hash = status.get("current_hash")
        schema_version = status.get("schema_version")
    else:
        raise OlimpoError("OLIMPO_INTERNAL_ERROR", status=500)
    payload: dict[str, Any] = {
        "schema_version": "olimpo.config-status.v0",
        "available": bool(available),
    }
    if isinstance(current_hash, str):
        payload["current_hash"] = current_hash
    if isinstance(schema_version, str):
        payload["config_schema_version"] = schema_version
    return payload


def project_preview(result: object) -> dict[str, Any]:
    fields = (
        "ok",
        "reason_code",
        "current_hash",
        "proposed_hash",
        "changes",
        "validation_status",
    )
    payload: dict[str, Any] = {}
    for name in fields:
        value = getattr(result, name, None) if not isinstance(result, dict) else result.get(name)
        if value is None:
            continue
        if name == "changes" and isinstance(value, (list, tuple)):
            payload[name] = [redact_string(str(item)) for item in value]
        elif name == "reason_code":
            if value not in STABLE_REASON_CODES:
                raise OlimpoError("OLIMPO_INTERNAL_ERROR", status=500)
            payload[name] = value
        else:
            payload[name] = sanitize_scalar(value)
    return payload


def project_apply(result: object) -> dict[str, Any]:
    return project_preview(result)


def dumps_response(payload: dict[str, Any]) -> bytes:
    encoded = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    if len(encoded) > MAX_RESPONSE_BYTES:
        raise OlimpoError("OLIMPO_RESPONSE_TOO_LARGE", status=500)
    return encoded


def error_payload(reason_code: str) -> dict[str, Any]:
    if reason_code not in STABLE_REASON_CODES:
        reason_code = "OLIMPO_INTERNAL_ERROR"
    return {"ok": False, "reason_code": reason_code}
