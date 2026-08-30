"""Testes focados O-0 — adapter HTTP local OLIMPO-0."""

from __future__ import annotations

import importlib
import json
import sys
import urllib.request
from dataclasses import dataclass
from http.client import HTTPConnection
from pathlib import Path
from typing import Any

import pytest

from athena.olimpo import (
    CSRF_HEADER,
    JSON_CONTENT_TYPE,
    LOOPBACK_HOST,
    MAX_LIST_ITEMS,
    MAX_REQUEST_BYTES,
    ROUTE_CONFIG_APPLY,
    ROUTE_CONFIG_PREVIEW,
    ROUTE_HEALTH,
    OlimpoDependencies,
    OlimpoError,
    OlimpoHttpServer,
    is_port_open,
)
from athena.olimpo.contracts import (
    ClioStatusSnapshot,
    ConfigApplyResult,
    ConfigPreviewResult,
    ConfigSnapshotStatus,
    InventoryEntry,
    validate_allowed_origins,
    validate_csrf_token,
)
from athena.olimpo.sanitize import redact_string
from athena.olimpo.static_files import (
    inject_csrf_bootstrap,
    serve_static,
    style_unsafe_inline_reason,
)
from athena.tasks.contracts import TaskRecord

ORIGIN = "http://127.0.0.1:5173"
TEST_CSRF = "a" * 64
WRONG_CSRF = "b" * 64


@dataclass(frozen=True)
class _StubTask:
    task_handle: str
    task_type: str
    state: str
    priority: int
    revision: int
    created_at: str
    updated_at: str
    reason_codes: tuple[str, ...] | None = None


class _TaskReader:
    def __init__(self) -> None:
        self._records = {
            "task-001": _StubTask(
                task_handle="task-001",
                task_type="run",
                state="queued",
                priority=5,
                revision=1,
                created_at="2026-08-29T00:00:00+00:00",
                updated_at="2026-08-29T00:00:00+00:00",
                reason_codes=("ALL_CHECKS_PASSED",),
            ),
            "secret-task": _StubTask(
                task_handle="secret-task",
                task_type="token=bearer sk-live",
                state="queued",
                priority=1,
                revision=1,
                created_at="2026-08-29T00:00:00+00:00",
                updated_at="2026-08-29T00:00:00+00:00",
            ),
        }

    def get_task(self, task_handle: str) -> object | None:
        return self._records.get(task_handle)

    def list_tasks(self, *, limit: int) -> list[object]:
        return list(self._records.values())[:limit]


class _ExecutionReader:
    def __init__(self) -> None:
        self._entries = {
            "exec-001": {
                "execution_id": "exec-001",
                "request_id": "Bearer sk-secret-token",
                "tool": "run_combo",
                "state": "completed",
                "attempts": [
                    {
                        "attempt_id": "a1",
                        "provider": "ollama",
                        "state": "completed",
                        "command": "must-not-leak",
                    }
                ],
            }
        }

    def get_execution(self, execution_id: str) -> object | None:
        return self._entries.get(execution_id)

    def list_executions(self, *, limit: int) -> list[object]:
        return list(self._entries.values())[:limit]


class _ClioReader:
    def read_status(self) -> ClioStatusSnapshot:
        return ClioStatusSnapshot(
            level="technical",
            storage="available",
            counters={"enqueued": 3, "writer_failures": 0},
        )


class _InventoryReader:
    def read_inventory(self) -> list[InventoryEntry]:
        return [
            InventoryEntry(
                provider_id="ollama",
                mode="local",
                runtime_class="local",
                enabled=True,
                approved=True,
                default_model="llama",
                availability="implemented",
            )
        ]


class _ConfigReader:
    def __init__(self, current_hash: str = "a" * 64) -> None:
        self.current_hash = current_hash

    def read_status(self) -> ConfigSnapshotStatus:
        return ConfigSnapshotStatus(
            available=True,
            current_hash=self.current_hash,
            schema_version="athena.config.v1.1",
        )


class _ConfigValidator:
    def preview(
        self,
        manifest: dict[str, Any],
        *,
        expected_hash: str | None,
    ) -> ConfigPreviewResult:
        if manifest.get("reject"):
            return ConfigPreviewResult(
                ok=False,
                reason_code="OLIMPO_CONFIG_VALIDATION_FAILED",
            )
        proposed = manifest.get("hash", "b" * 64)
        return ConfigPreviewResult(
            ok=True,
            current_hash=expected_hash,
            proposed_hash=proposed,
            changes=("parts.providers.json",),
            validation_status="valid",
        )


class _ConfigPublisher:
    def __init__(self, current_hash: str) -> None:
        self.current_hash = current_hash
        self.published: list[tuple[str, dict[str, Any]]] = []
        self.fail_next = False

    def apply(
        self,
        manifest: dict[str, Any],
        *,
        expected_hash: str,
    ) -> ConfigApplyResult:
        if expected_hash != self.current_hash:
            return ConfigApplyResult(
                ok=False,
                reason_code="OLIMPO_CONFIG_CONFLICT",
                current_hash=self.current_hash,
            )
        if self.fail_next:
            self.fail_next = False
            return ConfigApplyResult(
                ok=False,
                reason_code="OLIMPO_CONFIG_PUBLISH_FAILED",
                current_hash=self.current_hash,
            )
        self.current_hash = manifest.get("hash", "c" * 64)
        self.published.append((expected_hash, manifest))
        return ConfigApplyResult(ok=True, applied_hash=self.current_hash)


@pytest.fixture()
def deps() -> OlimpoDependencies:
    return OlimpoDependencies(
        package_version="0.2.0",
        task_reader=_TaskReader(),
        execution_reader=_ExecutionReader(),
        clio_reader=_ClioReader(),
        inventory_reader=_InventoryReader(),
        config_reader=_ConfigReader(),
        config_validator=_ConfigValidator(),
        config_publisher=_ConfigPublisher("a" * 64),
        allowed_origins=frozenset({ORIGIN}),
        csrf_token=TEST_CSRF,
    )


@pytest.fixture()
def server(deps: OlimpoDependencies) -> OlimpoHttpServer:
    srv = OlimpoHttpServer(deps, port=0)
    srv.start()
    yield srv
    srv.shutdown()


def _json(status: int, body: bytes) -> dict[str, Any]:
    assert body.endswith(b"\n")
    return json.loads(body.decode("utf-8"))


def _get(server: OlimpoHttpServer, path: str) -> tuple[int, dict[str, Any]]:
    status, body = server.handle_request(method="GET", path=path)
    return status, _json(status, body)


def _post(
    server: OlimpoHttpServer,
    path: str,
    payload: dict[str, Any],
    *,
    origin: str = ORIGIN,
    csrf: str = TEST_CSRF,
    content_type: str = JSON_CONTENT_TYPE,
) -> tuple[int, dict[str, Any]]:
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "content-type": content_type,
        "origin": origin,
        CSRF_HEADER.lower(): csrf,
    }
    status, raw = server.handle_request(
        method="POST",
        path=path,
        headers=headers,
        body=body,
    )
    return status, _json(status, raw)


def _raw_post(
    server: OlimpoHttpServer,
    path: str,
    body: bytes,
    extra_headers: list[tuple[str, str]],
) -> tuple[int, dict[str, Any]]:
    conn = HTTPConnection(server.host, server.port, timeout=2)
    conn.putrequest("POST", path)
    conn.putheader("Host", f"{server.host}:{server.port}")
    for name, value in extra_headers:
        conn.putheader(name, value)
    conn.endheaders(body)
    response = conn.getresponse()
    raw = response.read()
    conn.close()
    return response.status, _json(response.status, raw)


def test_health_read(server: OlimpoHttpServer) -> None:
    status, payload = _get(server, ROUTE_HEALTH)
    assert status == 200
    assert payload["schema_version"] == "olimpo.v0"
    assert payload["package_version"] == "0.2.0"
    assert payload["capabilities"]["frontend"] == "planned"
    assert payload["capabilities"]["tasks"] == "implemented"


def test_task_and_execution_reads(server: OlimpoHttpServer) -> None:
    status, task = _get(server, "/olimpo/v0/tasks/task-001")
    assert status == 200
    assert task["found"] is True
    assert task["task_handle"] == "task-001"

    status, tasks = _get(server, "/olimpo/v0/tasks?limit=1")
    assert status == 200
    assert len(tasks["items"]) == 1

    status, execution = _get(server, "/olimpo/v0/executions/exec-001")
    assert status == 200
    assert execution["execution_id"] == "exec-001"
    assert "command" not in execution["attempts"][0]


def test_clio_inventory_config_reads(server: OlimpoHttpServer) -> None:
    status, clio = _get(server, "/olimpo/v0/clio/status")
    assert status == 200
    assert clio["level"] == "technical"

    status, inventory = _get(server, "/olimpo/v0/inventory")
    assert status == 200
    assert inventory["items"][0]["provider_id"] == "ollama"

    status, config = _get(server, "/olimpo/v0/config")
    assert status == 200
    assert config["available"] is True


def test_redaction_on_task_and_execution(server: OlimpoHttpServer) -> None:
    status, task = _get(server, "/olimpo/v0/tasks/secret-task")
    assert status == 200
    assert "sk-live" not in task["task_type"]
    assert task["task_type"] == "[redacted]"

    status, execution = _get(server, "/olimpo/v0/executions/exec-001")
    assert status == 200
    assert execution["request_id"] == "[redacted]"


def test_invalid_route_and_method(server: OlimpoHttpServer) -> None:
    status, payload = _get(server, "/olimpo/v0/unknown")
    assert status == 404
    assert payload["reason_code"] == "OLIMPO_ROUTE_NOT_FOUND"

    status, raw = server.handle_request(method="PUT", path=ROUTE_HEALTH)
    assert status == 405
    body = _json(status, raw)
    assert body["reason_code"] == "OLIMPO_METHOD_NOT_ALLOWED"


def test_invalid_content_type_origin_csrf(server: OlimpoHttpServer) -> None:
    body = json.dumps({"manifest": {"hash": "b" * 64}}).encode("utf-8")
    status, raw = server.handle_request(
        method="POST",
        path=ROUTE_CONFIG_PREVIEW,
        headers={
            "content-type": "text/plain",
            "origin": ORIGIN,
            CSRF_HEADER.lower(): TEST_CSRF,
        },
        body=body,
    )
    assert status == 415
    assert _json(status, raw)["reason_code"] == "OLIMPO_CONTENT_TYPE_INVALID"

    status, payload = _post(
        server,
        ROUTE_CONFIG_PREVIEW,
        {"manifest": {"hash": "b" * 64}},
        origin="http://evil.test",
    )
    assert status == 403
    assert payload["reason_code"] == "OLIMPO_ORIGIN_FORBIDDEN"

    status, payload = _post(
        server,
        ROUTE_CONFIG_PREVIEW,
        {"manifest": {"hash": "b" * 64}},
        csrf=WRONG_CSRF,
    )
    assert status == 403
    assert payload["reason_code"] == "OLIMPO_CSRF_INVALID"


def test_request_byte_limit(server: OlimpoHttpServer) -> None:
    oversized = b"x" * (MAX_REQUEST_BYTES + 1)
    status, raw = server.handle_request(
        method="POST",
        path=ROUTE_CONFIG_PREVIEW,
        headers={
            "content-type": JSON_CONTENT_TYPE,
            "origin": ORIGIN,
            CSRF_HEADER.lower(): TEST_CSRF,
        },
        body=oversized,
    )
    assert status == 413
    assert _json(status, raw)["reason_code"] == "OLIMPO_REQUEST_TOO_LARGE"


def test_list_limit_bounds(server: OlimpoHttpServer) -> None:
    status, payload = _get(server, f"/olimpo/v0/tasks?limit={MAX_LIST_ITEMS + 1}")
    assert status == 400
    assert payload["reason_code"] == "OLIMPO_FIELD_INVALID"


def test_malformed_and_duplicate_json(server: OlimpoHttpServer) -> None:
    status, raw = server.handle_request(
        method="POST",
        path=ROUTE_CONFIG_PREVIEW,
        headers={
            "content-type": JSON_CONTENT_TYPE,
            "origin": ORIGIN,
            CSRF_HEADER.lower(): TEST_CSRF,
        },
        body=b"{",
    )
    assert status == 400
    assert _json(status, raw)["reason_code"] == "OLIMPO_JSON_INVALID"

    status, raw = server.handle_request(
        method="POST",
        path=ROUTE_CONFIG_PREVIEW,
        headers={
            "content-type": JSON_CONTENT_TYPE,
            "origin": ORIGIN,
            CSRF_HEADER.lower(): TEST_CSRF,
        },
        body=b'{"manifest": {}, "manifest": {}}',
    )
    assert status == 400
    assert _json(status, raw)["reason_code"] == "OLIMPO_JSON_DUPLICATE_KEY"


def test_preview_forbidden_field(server: OlimpoHttpServer) -> None:
    status, payload = _post(
        server,
        ROUTE_CONFIG_PREVIEW,
        {"manifest": {}, "prompt": "leak"},
    )
    assert status == 400
    assert payload["reason_code"] == "OLIMPO_FIELD_FORBIDDEN"


def test_config_preview_apply_and_cas(server: OlimpoHttpServer) -> None:
    status, preview = _post(
        server,
        ROUTE_CONFIG_PREVIEW,
        {"expected_hash": "a" * 64, "manifest": {"hash": "b" * 64}},
    )
    assert status == 200
    assert preview["ok"] is True
    assert preview["validation_status"] == "valid"

    status, applied = _post(
        server,
        ROUTE_CONFIG_APPLY,
        {"expected_hash": "a" * 64, "manifest": {"hash": "c" * 64}},
    )
    assert status == 200
    assert applied["ok"] is True

    status, conflict = _post(
        server,
        ROUTE_CONFIG_APPLY,
        {"expected_hash": "a" * 64, "manifest": {"hash": "d" * 64}},
    )
    assert status == 409
    assert conflict["reason_code"] == "OLIMPO_CONFIG_CONFLICT"


def test_validator_rejection(server: OlimpoHttpServer) -> None:
    status, payload = _post(
        server,
        ROUTE_CONFIG_PREVIEW,
        {"manifest": {"reject": True}},
    )
    assert status == 200
    assert payload["ok"] is False
    assert payload["reason_code"] == "OLIMPO_CONFIG_VALIDATION_FAILED"


def test_publisher_failure_preserves_state(server: OlimpoHttpServer, deps: OlimpoDependencies) -> None:
    publisher = deps.config_publisher
    assert isinstance(publisher, _ConfigPublisher)
    publisher.fail_next = True
    before = publisher.current_hash
    status, payload = _post(
        server,
        ROUTE_CONFIG_APPLY,
        {"expected_hash": before, "manifest": {"hash": "e" * 64}},
    )
    assert status == 409
    assert payload["reason_code"] == "OLIMPO_CONFIG_PUBLISH_FAILED"
    assert publisher.current_hash == before
    assert publisher.published == []


def test_loopback_bind_and_client_rejection(deps: OlimpoDependencies) -> None:
    with pytest.raises(OlimpoError) as exc:
        OlimpoHttpServer(deps, host="0.0.0.0")
    assert exc.value.reason_code == "OLIMPO_BIND_FORBIDDEN"

    server = OlimpoHttpServer(deps, port=0)
    status, payload = server.handle_request(
        method="GET",
        path=ROUTE_HEALTH,
        client_host="10.0.0.1",
    )
    assert status == 403
    assert _json(status, payload)["reason_code"] == "OLIMPO_CLIENT_NOT_LOOPBACK"


def test_http_server_loopback_only(server: OlimpoHttpServer) -> None:
    url = f"{server.base_url}{ROUTE_HEALTH}"
    with urllib.request.urlopen(url, timeout=2) as response:
        payload = json.loads(response.read().decode("utf-8"))
    assert payload["schema_version"] == "olimpo.v0"


def test_get_does_not_mutate_config(server: OlimpoHttpServer, deps: OlimpoDependencies) -> None:
    publisher = deps.config_publisher
    assert isinstance(publisher, _ConfigPublisher)
    before_hash = publisher.current_hash
    before_published = list(publisher.published)
    _get(server, "/olimpo/v0/config")
    _get(server, ROUTE_HEALTH)
    assert publisher.current_hash == before_hash
    assert publisher.published == before_published


def test_no_startup_side_effect_on_import() -> None:
    module_name = "athena.olimpo"
    if module_name in sys.modules:
        del sys.modules[module_name]
    importlib.import_module(module_name)
    assert not is_port_open(LOOPBACK_HOST, 17845)


def test_task_not_found(server: OlimpoHttpServer) -> None:
    status, payload = _get(server, "/olimpo/v0/tasks/missing-task")
    assert status == 404
    assert payload["reason_code"] == "OLIMPO_TASK_NOT_FOUND"


def test_redact_string_helper() -> None:
    assert redact_string("Bearer sk-secret") == "[redacted]"
    assert "/Users/me/project" not in redact_string("/Users/me/project/file")


def test_task_record_projection_redacts_forbidden_fields() -> None:
    record = TaskRecord(
        task_handle="task-001",
        task_type="safe",
        state="queued",
        priority=1,
        revision=1,
        created_at="t",
        updated_at="t",
        reason_codes=("ALL_CHECKS_PASSED",),
    )
    deps = OlimpoDependencies(
        package_version="0.2.0",
        task_reader=_TaskReader(),
        csrf_token="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    )
    server = OlimpoHttpServer(deps, port=0)
    status, payload = _get(server, f"/olimpo/v0/tasks/{record.task_handle}")
    assert status == 200
    assert "prompt" not in payload


def test_query_schema_rejects_unknown_blank_duplicate_malformed(
    server: OlimpoHttpServer,
) -> None:
    status, payload = _get(server, "/olimpo/v0/tasks?foo=1")
    assert status == 400
    assert payload["reason_code"] == "OLIMPO_FIELD_INVALID"

    status, payload = _get(server, "/olimpo/v0/tasks?limit=")
    assert status == 400
    assert payload["reason_code"] == "OLIMPO_FIELD_INVALID"

    status, payload = _get(server, "/olimpo/v0/tasks?limit=1&limit=2")
    assert status == 400
    assert payload["reason_code"] == "OLIMPO_FIELD_INVALID"

    status, payload = _get(server, "/olimpo/v0/tasks?limit=abc")
    assert status == 400
    assert payload["reason_code"] == "OLIMPO_FIELD_INVALID"


def test_http_handler_rejects_transfer_encoding_and_bad_content_length(
    server: OlimpoHttpServer,
) -> None:
    body = json.dumps({"manifest": {"hash": "b" * 64}}).encode("utf-8")
    base_headers = [
        ("Content-Type", JSON_CONTENT_TYPE),
        ("Origin", ORIGIN),
        (CSRF_HEADER, TEST_CSRF),
    ]

    status, payload = _raw_post(
        server,
        ROUTE_CONFIG_PREVIEW,
        body,
        base_headers
        + [
            ("Transfer-Encoding", "chunked"),
            ("Content-Length", str(len(body))),
        ],
    )
    assert status == 400
    assert payload["reason_code"] == "OLIMPO_FIELD_INVALID"

    status, payload = _raw_post(server, ROUTE_CONFIG_PREVIEW, body, base_headers)
    assert status == 400
    assert payload["reason_code"] == "OLIMPO_FIELD_INVALID"

    status, payload = _raw_post(
        server,
        ROUTE_CONFIG_PREVIEW,
        body,
        base_headers + [("Content-Length", "-1")],
    )
    assert status == 400
    assert payload["reason_code"] == "OLIMPO_FIELD_INVALID"

    status, payload = _raw_post(
        server,
        ROUTE_CONFIG_PREVIEW,
        body,
        base_headers + [("Content-Length", "not-a-number")],
    )
    assert status == 400
    assert payload["reason_code"] == "OLIMPO_FIELD_INVALID"

    status, payload = _raw_post(
        server,
        ROUTE_CONFIG_PREVIEW,
        body,
        base_headers + [("Content-Length", str(MAX_REQUEST_BYTES + 1))],
    )
    assert status == 413
    assert payload["reason_code"] == "OLIMPO_REQUEST_TOO_LARGE"


def test_http_handler_rejects_duplicate_security_headers(server: OlimpoHttpServer) -> None:
    body = json.dumps({"manifest": {"hash": "b" * 64}}).encode("utf-8")
    base = [
        ("Content-Type", JSON_CONTENT_TYPE),
        ("Content-Length", str(len(body))),
        ("Origin", ORIGIN),
        (CSRF_HEADER, TEST_CSRF),
    ]

    status, payload = _raw_post(
        server,
        ROUTE_CONFIG_PREVIEW,
        body,
        base + [("Origin", "http://evil.test")],
    )
    assert status == 400
    assert payload["reason_code"] == "OLIMPO_FIELD_INVALID"

    status, payload = _raw_post(
        server,
        ROUTE_CONFIG_PREVIEW,
        body,
        base + [(CSRF_HEADER, WRONG_CSRF)],
    )
    assert status == 400
    assert payload["reason_code"] == "OLIMPO_FIELD_INVALID"

    status, payload = _raw_post(
        server,
        ROUTE_CONFIG_PREVIEW,
        body,
        base + [("Content-Length", str(len(body) + 1))],
    )
    assert status == 400
    assert payload["reason_code"] == "OLIMPO_FIELD_INVALID"

    status, payload = _raw_post(
        server,
        ROUTE_CONFIG_PREVIEW,
        body,
        base + [("Content-Type", "text/plain")],
    )
    assert status == 400
    assert payload["reason_code"] == "OLIMPO_FIELD_INVALID"


def test_unexpected_reader_exception_returns_internal_error(
    deps: OlimpoDependencies,
) -> None:
    class _ExplodingReader:
        def get_task(self, task_handle: str) -> object | None:
            raise RuntimeError("secret leak")

        def list_tasks(self, *, limit: int) -> list[object]:
            raise RuntimeError("secret leak")

    exploding_deps = OlimpoDependencies(
        package_version=deps.package_version,
        task_reader=_ExplodingReader(),
        csrf_token=deps.csrf_token,
    )
    server = OlimpoHttpServer(exploding_deps, port=0)
    status, payload = _get(server, "/olimpo/v0/tasks/task-001")
    assert status == 500
    assert payload["reason_code"] == "OLIMPO_INTERNAL_ERROR"
    assert "secret" not in json.dumps(payload)


def test_csrf_token_validation_rejects_unsafe_values() -> None:
    with pytest.raises(OlimpoError) as exc:
        validate_csrf_token("short")
    assert exc.value.reason_code == "OLIMPO_CSRF_INVALID"

    with pytest.raises(OlimpoError) as exc:
        validate_csrf_token("</script><script>alert(1)</script>" + "a" * 32)
    assert exc.value.reason_code == "OLIMPO_CSRF_INVALID"

    with pytest.raises(OlimpoError) as exc:
        inject_csrf_bootstrap(b"<html></html>", "</script>", nonce="nonce123")
    assert exc.value.reason_code == "OLIMPO_CSRF_INVALID"


def test_html_csp_nonce_matches_inline_bootstrap(tmp_path: Path) -> None:
    static_root = tmp_path / "static"
    static_root.mkdir()
    (static_root / "index.html").write_text(
        "<html><head></head><body></body></html>",
        encoding="utf-8",
    )
    response = serve_static(static_root, "/", csrf_token=TEST_CSRF)
    csp = response.headers["Content-Security-Policy"]
    assert "script-src 'self' 'nonce-" in csp
    assert "unsafe-inline" not in csp.split("script-src", 1)[1].split(";", 1)[0]
    assert "style-src 'self' 'unsafe-inline'" in csp
    assert "connect-src 'self'" in csp
    assert style_unsafe_inline_reason()
    nonce = csp.split("'nonce-", 1)[1].split("'", 1)[0]
    body = response.body.decode("utf-8")
    assert f'nonce="{nonce}"' in body
    assert f"'nonce-{nonce}'" in csp
    assert TEST_CSRF in body


def test_static_root_symlink_rejected(tmp_path: Path) -> None:
    real_root = tmp_path / "real"
    real_root.mkdir()
    (real_root / "index.html").write_text("<html></html>", encoding="utf-8")
    link_root = tmp_path / "link-root"
    link_root.symlink_to(real_root)
    with pytest.raises(OlimpoError) as exc:
        serve_static(link_root, "/")
    assert exc.value.reason_code == "OLIMPO_STATIC_FORBIDDEN"


def test_symlink_path_component_rejected(tmp_path: Path) -> None:
    static_root = tmp_path / "static"
    static_root.mkdir()
    (static_root / "index.html").write_text("<html></html>", encoding="utf-8")
    (static_root / "secret.txt").write_text("secret", encoding="utf-8")
    (static_root / "link-in").symlink_to("secret.txt")
    (static_root / "link-out").symlink_to("/etc/passwd")
    allowed = static_root / "allowed.js"
    allowed.write_text("console.log(1)", encoding="utf-8")
    inside_link = static_root / "inside-link.js"
    inside_link.symlink_to("allowed.js")

    for path in ("/link-in", "/link-out", "/inside-link.js"):
        with pytest.raises(OlimpoError) as exc:
            serve_static(static_root, path)
        assert exc.value.reason_code == "OLIMPO_STATIC_FORBIDDEN"


def test_allowed_origins_reject_external_and_credentialed() -> None:
    with pytest.raises(OlimpoError) as exc:
        validate_allowed_origins(frozenset({"http://evil.test:5173"}))
    assert exc.value.reason_code == "OLIMPO_ORIGIN_FORBIDDEN"

    with pytest.raises(OlimpoError) as exc:
        validate_allowed_origins(frozenset({"http://user:pass@127.0.0.1:5173"}))
    assert exc.value.reason_code == "OLIMPO_ORIGIN_FORBIDDEN"

    validated = validate_allowed_origins(frozenset({ORIGIN}))
    assert validated == frozenset({ORIGIN})


def test_http_server_rejects_symlink_static_root(deps: OlimpoDependencies, tmp_path: Path) -> None:
    real_root = tmp_path / "real"
    real_root.mkdir()
    link_root = tmp_path / "link-root"
    link_root.symlink_to(real_root)
    symlink_deps = OlimpoDependencies(
        package_version=deps.package_version,
        csrf_token=deps.csrf_token,
        static_root=link_root,
    )
    with pytest.raises(OlimpoError) as exc:
        OlimpoHttpServer(symlink_deps, port=0, static_root=link_root)
    assert exc.value.reason_code == "OLIMPO_STATIC_FORBIDDEN"
