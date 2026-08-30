"""Roteamento fechado e handlers read-only/mutating de O-0."""

from __future__ import annotations

import secrets
from typing import Any
from urllib.parse import parse_qs, urlparse

from .contracts import (
    APPLY_REQUEST_FIELDS,
    CSRF_HEADER,
    GET_ROUTES,
    JSON_CONTENT_TYPE,
    LOOPBACK_HOST,
    MAX_LIST_ITEMS,
    MAX_REQUEST_BYTES,
    POST_ROUTES,
    PREVIEW_REQUEST_FIELDS,
    ROUTE_CLIO_STATUS,
    ROUTE_CONFIG,
    ROUTE_CONFIG_APPLY,
    ROUTE_CONFIG_PREVIEW,
    ROUTE_EXECUTIONS,
    ROUTE_HEALTH,
    ROUTE_INVENTORY,
    ROUTE_TASKS,
    SCHEMA_VERSION,
    HealthStatus,
    OlimpoDependencies,
    OlimpoError,
    validate_csrf_token,
    validate_hash,
    validate_identifier,
    validate_loopback_origin,
)
from .sanitize import (
    dumps_response,
    error_payload,
    parse_json_object,
    project_apply,
    project_clio_status,
    project_config_status,
    project_execution,
    project_inventory,
    project_preview,
    project_task,
)
from .static_files import StaticResponse, api_response, error_response, serve_static


def _parse_limit(query: str) -> int:
    if not query:
        return 50
    params = parse_qs(query, keep_blank_values=True)
    unknown = set(params) - {"limit"}
    if unknown:
        raise OlimpoError("OLIMPO_FIELD_INVALID")
    if "limit" not in params:
        return 50
    values = params["limit"]
    if len(values) != 1:
        raise OlimpoError("OLIMPO_FIELD_INVALID")
    raw = values[0]
    if raw == "":
        raise OlimpoError("OLIMPO_FIELD_INVALID")
    try:
        limit = int(raw, 10)
    except (TypeError, ValueError) as exc:
        raise OlimpoError("OLIMPO_FIELD_INVALID") from exc
    if limit < 1 or limit > MAX_LIST_ITEMS:
        raise OlimpoError("OLIMPO_FIELD_INVALID")
    return limit


def _validate_closed_fields(payload: dict[str, Any], allowed: frozenset[str]) -> None:
    extra = set(payload) - allowed
    if extra:
        raise OlimpoError("OLIMPO_FIELD_FORBIDDEN")


def _validate_manifest(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OlimpoError("OLIMPO_FIELD_INVALID")
    return value


class OlimpoApp:
    """Aplicação HTTP fechada — não inicia servidor por si só."""

    def __init__(self, dependencies: OlimpoDependencies) -> None:
        self._deps = dependencies

    def handle(
        self,
        *,
        method: str,
        path: str,
        query: str,
        headers: dict[str, str],
        body: bytes,
        client_host: str,
    ) -> StaticResponse:
        try:
            if client_host not in {LOOPBACK_HOST, "::1"}:
                raise OlimpoError("OLIMPO_CLIENT_NOT_LOOPBACK", status=403)
            route, suffix = self._resolve_route(path)
            if route is None:
                return self._handle_static(method, path)
            if method == "HEAD":
                response = self._dispatch_read(route, suffix, query, headers, body)
                return StaticResponse(
                    status=response.status,
                    body=b"",
                    content_type=response.content_type,
                    headers=response.headers,
                )
            if method == "GET":
                return self._dispatch_read(route, suffix, query, headers, body)
            if method == "POST":
                return self._dispatch_post(route, headers, body)
            raise OlimpoError("OLIMPO_METHOD_NOT_ALLOWED", status=405)
        except OlimpoError as exc:
            return error_response(exc.reason_code, status=exc.status)
        except Exception:  # noqa: BLE001 — injected readers must not leak faults
            return error_response("OLIMPO_INTERNAL_ERROR", status=500)

    def _dispatch_read(
        self,
        route: str,
        suffix: str | None,
        query: str,
        headers: dict[str, str],
        body: bytes,
    ) -> StaticResponse:
        _ = headers, body
        if route in POST_ROUTES:
            raise OlimpoError("OLIMPO_METHOD_NOT_ALLOWED", status=405)
        if route not in GET_ROUTES and not (
            route in {ROUTE_TASKS, ROUTE_EXECUTIONS} and suffix
        ):
            raise OlimpoError("OLIMPO_ROUTE_NOT_FOUND", status=404)
        status, payload = self._handle_get(route, suffix, query)
        return self._json(status, payload)

    def _dispatch_post(
        self,
        route: str,
        headers: dict[str, str],
        body: bytes,
    ) -> StaticResponse:
        if route not in POST_ROUTES:
            raise OlimpoError("OLIMPO_METHOD_NOT_ALLOWED", status=405)
        status, payload = self._handle_post(route, headers, body)
        return self._json(status, payload)

    def _handle_static(self, method: str, path: str) -> StaticResponse:
        if method not in {"GET", "HEAD"}:
            raise OlimpoError("OLIMPO_METHOD_NOT_ALLOWED", status=405)
        root = self._deps.static_root
        if root is None:
            raise OlimpoError("OLIMPO_ROUTE_NOT_FOUND", status=404)
        return serve_static(
            root,
            path,
            csrf_token=self._deps.csrf_token,
            head_only=method == "HEAD",
        )

    def _resolve_route(self, path: str) -> tuple[str | None, str | None]:
        parsed = urlparse(path)
        normalized = parsed.path.rstrip("/") or "/"
        if normalized in GET_ROUTES or normalized in POST_ROUTES:
            return normalized, None
        if normalized.startswith(f"{ROUTE_TASKS}/"):
            return ROUTE_TASKS, normalized[len(ROUTE_TASKS) + 1 :]
        if normalized.startswith(f"{ROUTE_EXECUTIONS}/"):
            return ROUTE_EXECUTIONS, normalized[len(ROUTE_EXECUTIONS) + 1 :]
        return None, None

    def _handle_get(
        self,
        route: str,
        suffix: str | None,
        query: str,
    ) -> tuple[int, bytes]:
        if route == ROUTE_HEALTH:
            return self._ok(self._health_payload())
        if route == ROUTE_TASKS:
            if suffix:
                return self._ok(self._task_by_handle(suffix))
            return self._ok(self._list_tasks(query))
        if route == ROUTE_EXECUTIONS:
            if suffix:
                return self._ok(self._execution_by_id(suffix))
            return self._ok(self._list_executions(query))
        if route == ROUTE_CLIO_STATUS:
            return self._ok(self._clio_status())
        if route == ROUTE_INVENTORY:
            return self._ok(self._inventory())
        if route == ROUTE_CONFIG:
            return self._ok(self._config_status())
        return self._error("OLIMPO_ROUTE_NOT_FOUND", status=404)

    def _handle_post(
        self,
        route: str,
        headers: dict[str, str],
        body: bytes,
    ) -> tuple[int, bytes]:
        self._validate_write_headers(headers)
        if len(body) > MAX_REQUEST_BYTES:
            return self._error("OLIMPO_REQUEST_TOO_LARGE", status=413)
        content_type = headers.get("content-type", "")
        if content_type.split(";", 1)[0].strip().lower() != JSON_CONTENT_TYPE:
            return self._error("OLIMPO_CONTENT_TYPE_INVALID", status=415)
        payload = parse_json_object(body)
        if route == ROUTE_CONFIG_PREVIEW:
            return self._config_preview(payload)
        if route == ROUTE_CONFIG_APPLY:
            return self._config_apply(payload)
        return self._error("OLIMPO_ROUTE_NOT_FOUND", status=404)

    def _validate_write_headers(self, headers: dict[str, str]) -> None:
        origin = headers.get("origin")
        if origin is None:
            raise OlimpoError("OLIMPO_ORIGIN_FORBIDDEN", status=403)
        validate_loopback_origin(origin)
        if origin not in self._deps.allowed_origins:
            raise OlimpoError("OLIMPO_ORIGIN_FORBIDDEN", status=403)
        token = headers.get(CSRF_HEADER.lower())
        expected = validate_csrf_token(self._deps.csrf_token)
        if not isinstance(token, str):
            raise OlimpoError("OLIMPO_CSRF_INVALID", status=403)
        safe_token = validate_csrf_token(token)
        if not secrets.compare_digest(safe_token, expected):
            raise OlimpoError("OLIMPO_CSRF_INVALID", status=403)

    def _health_payload(self) -> dict[str, Any]:
        status = HealthStatus(
            schema_version=SCHEMA_VERSION,
            package_version=self._deps.package_version,
            adapter_status="implemented",
            capabilities=self._deps.capabilities(),
        )
        caps = status.capabilities
        return {
            "schema_version": status.schema_version,
            "package_version": status.package_version,
            "adapter_status": status.adapter_status,
            "capabilities": {
                "health": caps.health,
                "tasks": caps.tasks,
                "executions": caps.executions,
                "clio": caps.clio,
                "inventory": caps.inventory,
                "config_preview": caps.config_preview,
                "config_apply": caps.config_apply,
                "frontend": caps.frontend,
            },
        }

    def _task_by_handle(self, handle: str) -> dict[str, Any]:
        if self._deps.task_reader is None:
            raise OlimpoError("OLIMPO_READER_UNAVAILABLE", status=503)
        validated = validate_identifier(handle, field_name="task_handle")
        record = self._deps.task_reader.get_task(validated)
        if record is None:
            raise OlimpoError("OLIMPO_TASK_NOT_FOUND", status=404)
        return project_task(record)

    def _list_tasks(self, query: str) -> dict[str, Any]:
        if self._deps.task_reader is None:
            raise OlimpoError("OLIMPO_READER_UNAVAILABLE", status=503)
        limit = _parse_limit(query)
        records = self._deps.task_reader.list_tasks(limit=limit)
        return {
            "schema_version": "olimpo.tasks.v0",
            "items": [project_task(record) for record in records[:limit]],
        }

    def _execution_by_id(self, execution_id: str) -> dict[str, Any]:
        if self._deps.execution_reader is None:
            raise OlimpoError("OLIMPO_READER_UNAVAILABLE", status=503)
        validated = validate_identifier(execution_id, field_name="execution_id")
        entry = self._deps.execution_reader.get_execution(validated)
        if entry is None:
            raise OlimpoError("OLIMPO_EXECUTION_NOT_FOUND", status=404)
        return project_execution(entry)

    def _list_executions(self, query: str) -> dict[str, Any]:
        if self._deps.execution_reader is None:
            raise OlimpoError("OLIMPO_READER_UNAVAILABLE", status=503)
        limit = _parse_limit(query)
        entries = self._deps.execution_reader.list_executions(limit=limit)
        return {
            "schema_version": "olimpo.executions.v0",
            "items": [project_execution(entry) for entry in entries[:limit]],
        }

    def _clio_status(self) -> dict[str, Any]:
        if self._deps.clio_reader is None:
            raise OlimpoError("OLIMPO_READER_UNAVAILABLE", status=503)
        return project_clio_status(self._deps.clio_reader.read_status())

    def _inventory(self) -> dict[str, Any]:
        if self._deps.inventory_reader is None:
            raise OlimpoError("OLIMPO_READER_UNAVAILABLE", status=503)
        return project_inventory(self._deps.inventory_reader.read_inventory())

    def _config_status(self) -> dict[str, Any]:
        if self._deps.config_reader is None:
            raise OlimpoError("OLIMPO_READER_UNAVAILABLE", status=503)
        return project_config_status(self._deps.config_reader.read_status())

    def _config_preview(self, payload: dict[str, Any]) -> tuple[int, bytes]:
        _validate_closed_fields(payload, PREVIEW_REQUEST_FIELDS)
        if self._deps.config_validator is None:
            raise OlimpoError("OLIMPO_READER_UNAVAILABLE", status=503)
        if "manifest" not in payload:
            raise OlimpoError("OLIMPO_FIELD_MISSING")
        expected_hash = payload.get("expected_hash")
        if expected_hash is not None:
            validate_hash(expected_hash)
        manifest = _validate_manifest(payload["manifest"])
        result = self._deps.config_validator.preview(
            manifest,
            expected_hash=expected_hash,
        )
        return self._ok(project_preview(result))

    def _config_apply(self, payload: dict[str, Any]) -> tuple[int, bytes]:
        _validate_closed_fields(payload, APPLY_REQUEST_FIELDS)
        if self._deps.config_publisher is None:
            raise OlimpoError("OLIMPO_READER_UNAVAILABLE", status=503)
        if "expected_hash" not in payload or "manifest" not in payload:
            raise OlimpoError("OLIMPO_FIELD_MISSING")
        expected_hash = validate_hash(payload["expected_hash"])
        manifest = _validate_manifest(payload["manifest"])
        result = self._deps.config_publisher.apply(
            manifest,
            expected_hash=expected_hash,
        )
        status = 200 if getattr(result, "ok", False) else 409
        return self._ok(project_apply(result), status=status)

    def _ok(self, payload: dict[str, Any], *, status: int = 200) -> tuple[int, bytes]:
        return status, dumps_response(payload)

    def _json(self, status: int, payload: bytes) -> StaticResponse:
        return api_response(status, payload, content_type=JSON_CONTENT_TYPE)

    def _error(self, reason_code: str, *, status: int = 400) -> tuple[int, bytes]:
        return status, dumps_response(error_payload(reason_code))
