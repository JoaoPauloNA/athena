"""Observação sombra Athena (opt-in, sem autoridade sobre execução)."""

from .contracts import (
    ALLOWED_EVENT_FIELDS,
    FORBIDDEN_FIELD_NAMES,
    ShadowExecutionEvent,
    ShadowObserverContract,
    validate_event_payload,
)
from .emitter import ENV_FLAG, ShadowEmitter, shadow_enabled

__all__ = [
    "ALLOWED_EVENT_FIELDS",
    "ENV_FLAG",
    "FORBIDDEN_FIELD_NAMES",
    "ShadowEmitter",
    "ShadowExecutionEvent",
    "ShadowObserverContract",
    "shadow_enabled",
    "validate_event_payload",
]
