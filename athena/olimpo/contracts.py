"""Contratos fechados do adapter HTTP local O-0 (OLIMPO-0)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

SCHEMA_VERSION = "olimpo.v0"
LOOPBACK_HOST = "127.0.0.1"

MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
MAX_LIST_ITEMS = 256
MAX_JSON_DEPTH = 32
MAX_JSON_ITEMS = 10_000
MAX_IDENTIFIER_LEN = 256

CSRF_HEADER = "X-Olimpo-CSRF-Token"
JSON_CONTENT_TYPE = "application/json"

ROUTE_HEALTH = "/olimpo/v0/health"
ROUTE_TASKS = "/olimpo/v0/tasks"
ROUTE_EXECUTIONS = "/olimpo/v0/executions"
ROUTE_CLIO_STATUS = "/olimpo/v0/clio/status"
ROUTE_INVENTORY = "/olimpo/v0/inventory"
ROUTE_CONFIG = "/olimpo/v0/config"
ROUTE_CONFIG_PREVIEW = "/olimpo/v0/config/preview"
ROUTE_CONFIG_APPLY = "/olimpo/v0/config/apply"

GET_ROUTES = frozenset(
    {
        ROUTE_HEALTH,
        ROUTE_TASKS,
        ROUTE_EXECUTIONS,
        ROUTE_CLIO_STATUS,
        ROUTE_INVENTORY,
        ROUTE_CONFIG,
    }
)
POST_ROUTES = frozenset({ROUTE_CONFIG_PREVIEW, ROUTE_CONFIG_APPLY})

CAPABILITY_IMPLEMENTED = "implemented"
CAPABILITY_UNAVAILABLE = "unavailable"
CAPABILITY_PLANNED = "planned"
VALID_CAPABILITIES = frozenset(
    {CAPABILITY_IMPLEMENTED, CAPABILITY_UNAVAILABLE, CAPABILITY_PLANNED}
)

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[a-z0-9-]+$")
_CSRF_TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")
_LOOPBACK_ORIGIN_RE = re.compile(r"^http://127\.0\.0\.1:\d+$")

STABLE_REASON_CODES = frozenset(
    {
        "OLIMPO_ROUTE_NOT_FOUND",
        "OLIMPO_METHOD_NOT_ALLOWED",
        "OLIMPO_CONTENT_TYPE_INVALID",
        "OLIMPO_ORIGIN_FORBIDDEN",
        "OLIMPO_CSRF_INVALID",
        "OLIMPO_REQUEST_TOO_LARGE",
        "OLIMPO_RESPONSE_TOO_LARGE",
        "OLIMPO_JSON_INVALID",
        "OLIMPO_JSON_DUPLICATE_KEY",
        "OLIMPO_FIELD_FORBIDDEN",
        "OLIMPO_FIELD_MISSING",
        "OLIMPO_FIELD_INVALID",
        "OLIMPO_TASK_NOT_FOUND",
        "OLIMPO_EXECUTION_NOT_FOUND",
        "OLIMPO_CONFIG_CONFLICT",
        "OLIMPO_CONFIG_VALIDATION_FAILED",
        "OLIMPO_CONFIG_PUBLISH_FAILED",
        "OLIMPO_BIND_FORBIDDEN",
        "OLIMPO_CLIENT_NOT_LOOPBACK",
        "OLIMPO_READER_UNAVAILABLE",
        "OLIMPO_INTERNAL_ERROR",
        "OLIMPO_STATIC_NOT_FOUND",
        "OLIMPO_STATIC_FORBIDDEN",
        "OLIMPO_STATIC_TOO_LARGE",
    }
)

FORBIDDEN_RESPONSE_KEYS = frozenset(
    {
        "prompt",
        "command",
        "argv",
        "cwd",
        "stdout",
        "stderr",
        "env",
        "environment",
        "output",
        "response",
        "message",
        "text",
        "input",
        "arguments",
        "token",
        "api_key",
        "secret",
        "credential",
        "password",
        "authorization",
        "secret_ref",
        "raw_url",
        "log",
        "logs",
    }
)

PREVIEW_REQUEST_FIELDS = frozenset({"expected_hash", "manifest"})
APPLY_REQUEST_FIELDS = frozenset({"expected_hash", "manifest"})


class OlimpoError(Exception):
    """Erro fechado do adapter com código sanitizado."""

    def __init__(self, reason_code: str, *, status: int = 400) -> None:
        if reason_code not in STABLE_REASON_CODES:
            raise ValueError(f"unstable reason_code: {reason_code!r}")
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.status = status


def validate_hash(value: object) -> str:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise OlimpoError("OLIMPO_FIELD_INVALID")
    return value


def validate_csrf_token(value: object) -> str:
    if not isinstance(value, str) or not _CSRF_TOKEN_RE.fullmatch(value):
        raise OlimpoError("OLIMPO_CSRF_INVALID", status=403)
    lowered = value.lower()
    if "</script" in lowered or "<" in value or ">" in value:
        raise OlimpoError("OLIMPO_CSRF_INVALID", status=403)
    return value


def validate_loopback_origin(value: object) -> str:
    if not isinstance(value, str) or not _LOOPBACK_ORIGIN_RE.fullmatch(value):
        raise OlimpoError("OLIMPO_ORIGIN_FORBIDDEN", status=403)
    if "@" in value:
        raise OlimpoError("OLIMPO_ORIGIN_FORBIDDEN", status=403)
    return value


def validate_allowed_origins(origins: frozenset[str]) -> frozenset[str]:
    return frozenset(validate_loopback_origin(origin) for origin in origins)


def validate_identifier(value: object, *, field_name: str = "identifier") -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_IDENTIFIER_LEN:
        raise OlimpoError("OLIMPO_FIELD_INVALID")
    if field_name.startswith("task") and not _ID_RE.fullmatch(value):
        raise OlimpoError("OLIMPO_FIELD_INVALID")
    if field_name.startswith("execution") and not value.strip():
        raise OlimpoError("OLIMPO_FIELD_INVALID")
    return value


@dataclass(frozen=True, slots=True)
class CapabilityStatus:
    health: str = CAPABILITY_IMPLEMENTED
    tasks: str = CAPABILITY_UNAVAILABLE
    executions: str = CAPABILITY_UNAVAILABLE
    clio: str = CAPABILITY_UNAVAILABLE
    inventory: str = CAPABILITY_UNAVAILABLE
    config_preview: str = CAPABILITY_UNAVAILABLE
    config_apply: str = CAPABILITY_UNAVAILABLE
    frontend: str = CAPABILITY_PLANNED

    def __post_init__(self) -> None:
        for name in (
            "health",
            "tasks",
            "executions",
            "clio",
            "inventory",
            "config_preview",
            "config_apply",
            "frontend",
        ):
            value = getattr(self, name)
            if value not in VALID_CAPABILITIES:
                raise ValueError(f"invalid capability value: {name}={value!r}")


@dataclass(frozen=True, slots=True)
class HealthStatus:
    schema_version: str
    package_version: str
    adapter_status: str
    capabilities: CapabilityStatus

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("invalid schema_version")
        if not isinstance(self.package_version, str) or not self.package_version:
            raise ValueError("invalid package_version")
        if self.adapter_status not in VALID_CAPABILITIES:
            raise ValueError("invalid adapter_status")


@dataclass(frozen=True, slots=True)
class ClioStatusSnapshot:
    level: str
    storage: str
    counters: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class InventoryEntry:
    provider_id: str | None = None
    function_id: str | None = None
    mode: str | None = None
    runtime_class: str | None = None
    enabled: bool | None = None
    approved: bool | None = None
    default_model: str | None = None
    specialist: str | None = None
    version: str | None = None
    min_status: str | None = None
    observed_discovered: bool | None = None
    observed_healthy: bool | None = None
    availability: str = CAPABILITY_UNAVAILABLE


@dataclass(frozen=True, slots=True)
class ConfigSnapshotStatus:
    available: bool
    current_hash: str | None = None
    schema_version: str | None = None


@dataclass(frozen=True, slots=True)
class ConfigPreviewResult:
    ok: bool
    reason_code: str | None = None
    current_hash: str | None = None
    proposed_hash: str | None = None
    changes: tuple[str, ...] = ()
    validation_status: str | None = None


@dataclass(frozen=True, slots=True)
class ConfigApplyResult:
    ok: bool
    reason_code: str | None = None
    applied_hash: str | None = None
    current_hash: str | None = None


@runtime_checkable
class TaskReader(Protocol):
    def get_task(self, task_handle: str) -> object | None:
        ...

    def list_tasks(self, *, limit: int) -> list[object]:
        ...


@runtime_checkable
class ExecutionReader(Protocol):
    def get_execution(self, execution_id: str) -> object | None:
        ...

    def list_executions(self, *, limit: int) -> list[object]:
        ...


@runtime_checkable
class ClioStatusReader(Protocol):
    def read_status(self) -> ClioStatusSnapshot:
        ...


@runtime_checkable
class InventoryReader(Protocol):
    def read_inventory(self) -> list[InventoryEntry]:
        ...


@runtime_checkable
class ConfigSnapshotReader(Protocol):
    def read_status(self) -> ConfigSnapshotStatus:
        ...


@runtime_checkable
class ConfigValidator(Protocol):
    def preview(
        self,
        manifest: dict[str, Any],
        *,
        expected_hash: str | None,
    ) -> ConfigPreviewResult:
        ...


@runtime_checkable
class ConfigPublisher(Protocol):
    def apply(
        self,
        manifest: dict[str, Any],
        *,
        expected_hash: str,
    ) -> ConfigApplyResult:
        ...


@dataclass(frozen=True, slots=True)
class OlimpoDependencies:
    package_version: str
    task_reader: TaskReader | None = None
    execution_reader: ExecutionReader | None = None
    clio_reader: ClioStatusReader | None = None
    inventory_reader: InventoryReader | None = None
    config_reader: ConfigSnapshotReader | None = None
    config_validator: ConfigValidator | None = None
    config_publisher: ConfigPublisher | None = None
    allowed_origins: frozenset[str] = frozenset({"http://127.0.0.1:5173"})
    csrf_token: str = ""
    static_root: Path | None = None

    def capabilities(self) -> CapabilityStatus:
        task_ready = isinstance(self.task_reader, TaskReader)
        return CapabilityStatus(
            health=CAPABILITY_IMPLEMENTED,
            tasks=(
                CAPABILITY_IMPLEMENTED if task_ready else CAPABILITY_UNAVAILABLE
            ),
            executions=(
                CAPABILITY_IMPLEMENTED
                if self.execution_reader is not None
                else CAPABILITY_UNAVAILABLE
            ),
            clio=(
                CAPABILITY_IMPLEMENTED
                if self.clio_reader is not None
                else CAPABILITY_UNAVAILABLE
            ),
            inventory=(
                CAPABILITY_IMPLEMENTED
                if self.inventory_reader is not None
                else CAPABILITY_UNAVAILABLE
            ),
            config_preview=(
                CAPABILITY_IMPLEMENTED
                if self.config_validator is not None
                else CAPABILITY_UNAVAILABLE
            ),
            config_apply=(
                CAPABILITY_IMPLEMENTED
                if self.config_publisher is not None
                else CAPABILITY_UNAVAILABLE
            ),
            frontend=(
                CAPABILITY_IMPLEMENTED
                if self.static_root is not None
                else CAPABILITY_PLANNED
            ),
        )
