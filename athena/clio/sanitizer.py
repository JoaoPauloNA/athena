"""Sanitização determinística e validação allowlist para eventos Clio."""

from __future__ import annotations

import json
import re
from typing import Any

from .contracts import (
    COMPLETE_FIELDS,
    FORBIDDEN_FIELD_NAMES,
    MAX_EVENT_BYTES,
    MAX_FIELD_LEN,
    MAX_REASON_CODES,
    MAX_TEXT_BYTES,
    PARTIAL_FIELDS,
    SCHEMA_VERSION,
    TECHNICAL_FIELDS,
    VALID_EVENT_TYPES,
    VALID_LEVELS,
    PartialSummaries,
    ProtectedEnvelope,
    TechnicalEvent,
)

_SECRET_URL = re.compile(r"https?://[^\s/]+:[^\s/@]+@", re.IGNORECASE)
_BEARER = re.compile(r"bearer\s+[a-z0-9._-]+", re.IGNORECASE)
_API_KEY = re.compile(r"(api[_-]?key|token|secret|password)\s*[:=]\s*\S+", re.IGNORECASE)
_IDENTIFIER_CHARS = re.compile(r"[^a-zA-Z0-9._-]")
_SECRET_MARKERS = re.compile(
    r"\b(bearer|token|secret|password|api_key|credential)\b",
    re.IGNORECASE,
)
_ISO8601_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?"
    r"(?:Z|[+-]\d{2}:\d{2})$"
)

TECHNICAL_IDENTIFIER_FIELDS = frozenset(
    {
        "task_handle",
        "execution_id",
        "tool",
        "provider",
        "model",
        "execution_status",
        "validation_status",
        "delivery_status",
        "chronos_action",
        "old_level",
        "new_level",
    }
)


def _truncate_bytes(text: str, limit: int) -> str:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return text
    clipped = encoded[:limit]
    while clipped and (clipped[-1] & 0xC0) == 0x80:
        clipped = clipped[:-1]
    return clipped.decode("utf-8", errors="replace")


def normalize_timestamp(value: object) -> str:
    """Validar e normalizar timestamp estrito UTC/offset ISO-8601."""
    raw = _safe_str(value, max_len=64)
    if not raw:
        raise ValueError("timestamp required")
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    if not _ISO8601_TIMESTAMP.match(normalized):
        raise ValueError("invalid timestamp")
    return normalized


def redact_text(text: str) -> str:
    """Remoção determinística de padrões sensíveis em resumos partial."""
    if not text:
        return ""
    cleaned = _SECRET_URL.sub("https://[REDACTED]@", text)
    cleaned = _BEARER.sub("bearer [REDACTED]", cleaned)
    cleaned = _API_KEY.sub(r"\1=[REDACTED]", cleaned)
    for forbidden in FORBIDDEN_FIELD_NAMES:
        cleaned = re.sub(
            rf"\b{re.escape(forbidden)}\b\s*[:=]\s*\S+",
            f"{forbidden}=[REDACTED]",
            cleaned,
            flags=re.IGNORECASE,
        )
    return _truncate_bytes(cleaned, MAX_TEXT_BYTES)


def normalize_identifier(value: object, *, max_len: int = MAX_FIELD_LEN) -> str:
    """Normaliza campos técnicos para charset allowlisted e tamanho limitado."""
    raw = _safe_str(value, max_len=max_len)
    if not raw:
        return ""
    cleaned = redact_text(raw)
    cleaned = _SECRET_MARKERS.sub("", cleaned)
    normalized = _IDENTIFIER_CHARS.sub("", cleaned)
    return _truncate_bytes(normalized, max_len)


def normalize_error_code(value: object) -> str:
    """Normaliza código de erro — sem segredos, charset restrito."""
    raw = _safe_str(value, max_len=64)
    if not raw:
        return ""
    cleaned = redact_text(raw)
    normalized = "".join(
        char for char in cleaned.upper() if char.isalnum() or char in "._-"
    )
    return _truncate_bytes(normalized, 64)


def _safe_str(value: object, *, max_len: int = MAX_FIELD_LEN) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return _truncate_bytes(value, max_len)


def _safe_int(value: object | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    return None


def _safe_reason_codes(codes: object) -> tuple[str, ...]:
    if not isinstance(codes, (list, tuple)):
        return ()
    result: list[str] = []
    for item in codes:
        if not isinstance(item, str) or not item:
            continue
        result.append(normalize_identifier(item, max_len=64))
        if len(result) >= MAX_REASON_CODES:
            break
    return tuple(result)


def validate_payload(payload: dict[str, Any]) -> None:
    """Falhar se payload violar allowlist ou contiver campos proibidos."""
    keys = set(payload)
    leaked = FORBIDDEN_FIELD_NAMES & keys
    if leaked:
        raise ValueError(f"prohibited fields: {sorted(leaked)}")
    level = payload.get("level")
    if level not in VALID_LEVELS:
        raise ValueError("invalid level")
    event_type = payload.get("event_type")
    if event_type not in VALID_EVENT_TYPES:
        raise ValueError("invalid event_type")
    normalize_timestamp(payload.get("timestamp"))
    allowed = set(TECHNICAL_FIELDS)
    if level == "partial":
        allowed |= PARTIAL_FIELDS
    if level == "complete":
        allowed |= COMPLETE_FIELDS
    extra = keys - allowed
    if extra:
        raise ValueError(f"fields outside allowlist: {sorted(extra)}")


def serialize_event(payload: dict[str, Any]) -> bytes:
    validate_payload(payload)
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(encoded) > MAX_EVENT_BYTES:
        raise ValueError("event exceeds byte limit")
    return encoded


def build_technical_payload(
    event: TechnicalEvent,
    *,
    level: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "event_type": event.event_type,
        "level": level,
        "timestamp": normalize_timestamp(event.timestamp),
    }
    optional_fields = (
        ("task_handle", event.task_handle),
        ("execution_id", event.execution_id),
        ("tool", event.tool),
        ("provider", event.provider),
        ("model", event.model),
        ("execution_status", event.execution_status),
        ("validation_status", event.validation_status),
        ("delivery_status", event.delivery_status),
        ("chronos_action", event.chronos_action),
        ("error_code", event.error_code),
        ("old_level", event.old_level),
        ("new_level", event.new_level),
    )
    for key, value in optional_fields:
        if key == "error_code":
            safe = normalize_error_code(value)
        elif key in TECHNICAL_IDENTIFIER_FIELDS:
            safe = normalize_identifier(value)
        else:
            safe = _safe_str(value)
        if safe:
            payload[key] = safe
    if event.attempts_used is not None:
        safe_int = _safe_int(event.attempts_used)
        if safe_int is not None:
            payload["attempts_used"] = safe_int
    for key, value in (
        ("duration_ms", event.duration_ms),
        ("queue_wait_ms", event.queue_wait_ms),
        ("timeout_ms", event.timeout_ms),
        ("retry_count", event.retry_count),
    ):
        safe_int = _safe_int(value)
        if safe_int is not None:
            payload[key] = safe_int
    if event.reason_codes:
        payload["reason_codes"] = list(_safe_reason_codes(event.reason_codes))
    return payload


def build_partial_payload(
    event: TechnicalEvent,
    summaries: PartialSummaries,
) -> dict[str, Any]:
    payload = build_technical_payload(event, level="partial")
    for key, raw in (
        ("request_summary", summaries.request_summary),
        ("constraints_summary", summaries.constraints_summary),
        ("decision_summary", summaries.decision_summary),
        ("result_summary", summaries.result_summary),
    ):
        redacted = redact_text(raw)
        if redacted:
            payload[key] = redacted
    return payload


def build_complete_payload(
    event: TechnicalEvent,
    envelope: ProtectedEnvelope,
) -> dict[str, Any]:
    payload = build_technical_payload(event, level="complete")
    if not envelope.algorithm or not envelope.payload_b64:
        raise ValueError("invalid protected envelope")
    payload["protected_envelope"] = {
        "algorithm": _safe_str(envelope.algorithm, max_len=64),
        "payload_b64": _safe_str(envelope.payload_b64, max_len=MAX_TEXT_BYTES),
    }
    if envelope.key_id:
        payload["protected_envelope"]["key_id"] = _safe_str(envelope.key_id, max_len=64)
    return payload
