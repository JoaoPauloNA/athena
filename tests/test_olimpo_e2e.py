"""E2E loopback O-2 — estático, segurança, adapters reais e CAS de config."""

from __future__ import annotations

import hashlib
import json
import re
from http.client import HTTPConnection
from pathlib import Path

import pytest

from athena.config_loader import build_manifest
from athena.olimpo import (
    CSRF_HEADER,
    JSON_CONTENT_TYPE,
    ROUTE_CONFIG,
    ROUTE_CONFIG_APPLY,
    ROUTE_CONFIG_PREVIEW,
    ROUTE_HEALTH,
    ROUTE_TASKS,
    CompositionSources,
    OlimpoError,
    OlimpoHttpServer,
    compose_dependencies,
)
from athena.registry import ExecutionRegistry
from athena.tasks.sqlite_store import SQLiteTaskStore
from athena.tasks.validation import build_submission
from tests.route0_support import write_route_config

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DIST_ROOT = _REPO_ROOT / "olimpo" / "dist"


class _PublicTaskReader:
    """Adapter público de E2E — lista só handles conhecidos via get_task."""

    def __init__(self, store: object, handles: tuple[str, ...]) -> None:
        self._store = store
        self._handles = handles

    def get_task(self, task_handle: str) -> object | None:
        return self._store.get_task(task_handle)  # type: ignore[union-attr]

    def list_tasks(self, *, limit: int) -> list[object]:
        records: list[object] = []
        for handle in self._handles[:limit]:
            record = self.get_task(handle)
            if record is not None:
                records.append(record)
        return records


def _request(
    server: OlimpoHttpServer,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    conn = HTTPConnection(server.host, server.port, timeout=3)
    conn.putrequest(method, path)
    conn.putheader("Host", f"{server.host}:{server.port}")
    for name, value in (headers or {}).items():
        conn.putheader(name, value)
    conn.endheaders()
    response = conn.getresponse()
    body = response.read()
    response_headers = {name.lower(): value for name, value in response.getheaders()}
    conn.close()
    return response.status, response_headers, body


def _json(method: str, path: str, server: OlimpoHttpServer) -> tuple[int, dict[str, object]]:
    status, _, body = _request(server, method, path)
    return status, json.loads(body.decode("utf-8"))


def _post_json(
    server: OlimpoHttpServer,
    path: str,
    payload: dict[str, object],
    *,
    origin: str | None = None,
) -> tuple[int, dict[str, object]]:
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": JSON_CONTENT_TYPE,
        "Content-Length": str(len(body)),
        "Origin": origin or server.base_url,
        CSRF_HEADER: server.csrf_token,
    }
    conn = HTTPConnection(server.host, server.port, timeout=3)
    conn.putrequest("POST", path)
    conn.putheader("Host", f"{server.host}:{server.port}")
    for name, value in headers.items():
        conn.putheader(name, value)
    conn.endheaders(body)
    response = conn.getresponse()
    raw = response.read()
    conn.close()
    return response.status, json.loads(raw.decode("utf-8"))


@pytest.fixture()
def dist_root() -> Path:
    if not _DIST_ROOT.is_dir():
        pytest.skip("olimpo/dist ausente — build frontend necessário para E2E estático")
    return _DIST_ROOT


@pytest.fixture()
def bare_server() -> OlimpoHttpServer:
    deps = compose_dependencies(CompositionSources(package_version="0.2.0"))
    server = OlimpoHttpServer(deps, port=0)
    server.start()
    yield server
    server.shutdown()


@pytest.fixture()
def static_server(dist_root: Path) -> OlimpoHttpServer:
    deps = compose_dependencies(
        CompositionSources(package_version="0.2.0", static_root=dist_root)
    )
    server = OlimpoHttpServer(deps, port=0, static_root=dist_root)
    server.start()
    yield server
    server.shutdown()


def test_index_bootstrap_injects_csrf_and_security_headers(static_server: OlimpoHttpServer) -> None:
    status, headers, body = _request(static_server, "GET", "/")
    assert status == 200
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["cache-control"] == "no-store"
    assert "content-security-policy" in headers
    csp = headers["content-security-policy"]
    assert "connect-src 'self'" in csp
    assert "style-src 'self' 'unsafe-inline'" in csp
    script_src = csp.split("script-src", 1)[1].split(";", 1)[0]
    assert "unsafe-inline" not in script_src
    nonce_match = re.search(r"'nonce-([^']+)'", csp)
    assert nonce_match is not None
    nonce = nonce_match.group(1)
    text = body.decode("utf-8")
    assert f'nonce="{nonce}"' in text
    assert "window.__OLIMPO_CSRF_TOKEN__" in text
    assert static_server.csrf_token in text


def test_static_asset_served_with_immutable_cache(static_server: OlimpoHttpServer) -> None:
    asset = next(_DIST_ROOT.glob("assets/index-*.js")).name
    status, headers, body = _request(static_server, "GET", f"/assets/{asset}")
    assert status == 200
    assert len(body) > 0
    assert "immutable" in headers.get("cache-control", "")


def test_static_head_returns_headers_without_body(static_server: OlimpoHttpServer) -> None:
    status, headers, body = _request(static_server, "HEAD", "/")
    assert status == 200
    assert body == b""
    assert headers.get("content-type", "").startswith("text/html")


def test_traversal_and_symlink_refused(tmp_path: Path) -> None:
    static_root = tmp_path / "static"
    static_root.mkdir()
    (static_root / "index.html").write_text("<html></html>", encoding="utf-8")
    (static_root / "secret.txt").write_text("secret", encoding="utf-8")
    (static_root / "link-out").symlink_to("/etc/passwd")
    (static_root / "link-in").symlink_to("secret.txt")
    allowed = static_root / "allowed.js"
    allowed.write_text("console.log(1)", encoding="utf-8")
    (static_root / "inside-link.js").symlink_to("allowed.js")

    deps = compose_dependencies(
        CompositionSources(package_version="0.2.0", static_root=static_root)
    )
    server = OlimpoHttpServer(deps, port=0, static_root=static_root)
    server.start()
    try:
        for path in ("/../secret.txt", "/link-out", "/link-in", "/inside-link.js"):
            status, payload = _json("GET", path, server)
            assert status in {403, 404}
            assert payload["reason_code"] in {
                "OLIMPO_STATIC_FORBIDDEN",
                "OLIMPO_STATIC_NOT_FOUND",
                "OLIMPO_ROUTE_NOT_FOUND",
            }
    finally:
        server.shutdown()


def test_static_root_symlink_rejected_at_server(tmp_path: Path) -> None:
    real_root = tmp_path / "real"
    real_root.mkdir()
    link_root = tmp_path / "link-root"
    link_root.symlink_to(real_root)
    deps = compose_dependencies(
        CompositionSources(package_version="0.2.0", static_root=link_root)
    )
    with pytest.raises(OlimpoError) as exc:
        OlimpoHttpServer(deps, port=0, static_root=link_root)
    assert exc.value.reason_code == "OLIMPO_STATIC_FORBIDDEN"


def test_unsupported_methods_return_json_not_html(static_server: OlimpoHttpServer) -> None:
    for method in ("PUT", "DELETE", "PATCH", "OPTIONS"):
        status, _, body = _request(static_server, method, ROUTE_HEALTH)
        assert status == 405
        payload = json.loads(body.decode("utf-8"))
        assert payload["reason_code"] == "OLIMPO_METHOD_NOT_ALLOWED"
        assert b"<html" not in body.lower()


def test_health_and_tasks_unavailable_vs_available(
    bare_server: OlimpoHttpServer,
    tmp_path: Path,
) -> None:
    status, health = _json("GET", ROUTE_HEALTH, bare_server)
    assert status == 200
    assert health["capabilities"]["tasks"] == "unavailable"
    assert health["capabilities"]["config_preview"] == "unavailable"

    store = SQLiteTaskStore(tmp_path / "state")
    submission = build_submission("key-e2e", {"task_type": "run", "input": "ok"})
    result = store.submit_task(submission)
    registry = ExecutionRegistry()
    registry.create(
        execution_id="exec-e2e",
        request_id="req-e2e",
        tool="run_combo",
    )

    store_only = compose_dependencies(
        CompositionSources(
            package_version="0.2.0",
            task_reader=store,
            execution_registry=registry,
        )
    )
    store_server = OlimpoHttpServer(store_only, port=0)
    store_server.start()
    try:
        _, store_health = _json("GET", ROUTE_HEALTH, store_server)
        assert store_health["capabilities"]["tasks"] == "unavailable"
        status, unavailable = _json("GET", ROUTE_TASKS, store_server)
        assert status == 503
        assert unavailable["reason_code"] == "OLIMPO_READER_UNAVAILABLE"
    finally:
        store_server.shutdown()

    deps = compose_dependencies(
        CompositionSources(
            package_version="0.2.0",
            task_reader=_PublicTaskReader(store, (result.task_handle,)),
            execution_registry=registry,
        )
    )
    server = OlimpoHttpServer(deps, port=0)
    server.start()
    try:
        status, health = _json("GET", ROUTE_HEALTH, server)
        assert health["capabilities"]["tasks"] == "implemented"
        assert health["capabilities"]["executions"] == "implemented"

        status, task = _json("GET", f"{ROUTE_TASKS}/{result.task_handle}", server)
        assert status == 200
        assert task["task_handle"] == result.task_handle

        status, listed = _json("GET", ROUTE_TASKS, server)
        assert status == 200
        assert len(listed["items"]) == 1
    finally:
        server.shutdown()


def test_config_preview_apply_cas(tmp_path: Path) -> None:
    config_dir = write_route_config(tmp_path / "cfg", providers=("ollama",))
    manifest = build_manifest(config_dir)
    current_hash = hashlib.sha256((config_dir / "snapshot.json").read_bytes()).hexdigest()

    deps = compose_dependencies(
        CompositionSources(package_version="0.2.0", config_dir=config_dir)
    )
    server = OlimpoHttpServer(deps, port=0)
    server.start()
    try:
        status, config_status = _json("GET", ROUTE_CONFIG, server)
        assert status == 200
        assert config_status["available"] is True

        status, preview = _post_json(
            server,
            ROUTE_CONFIG_PREVIEW,
            {"expected_hash": current_hash, "manifest": manifest},
            origin=server.base_url,
        )
        assert status == 200
        assert preview["ok"] is True

        status, applied = _post_json(
            server,
            ROUTE_CONFIG_APPLY,
            {"expected_hash": current_hash, "manifest": manifest},
            origin=server.base_url,
        )
        assert status == 200
        assert applied["ok"] is True

        status, conflict = _post_json(
            server,
            ROUTE_CONFIG_APPLY,
            {"expected_hash": "0" * 64, "manifest": manifest},
            origin=server.base_url,
        )
        assert status == 409
        assert conflict["reason_code"] == "OLIMPO_CONFIG_CONFLICT"
    finally:
        server.shutdown()


def test_missing_static_path_returns_bounded_json(bare_server: OlimpoHttpServer) -> None:
    status, payload = _json("GET", "/missing-asset.js", bare_server)
    assert status == 404
    assert payload["reason_code"] == "OLIMPO_ROUTE_NOT_FOUND"


def test_shutdown_releases_port(static_server: OlimpoHttpServer) -> None:
    port = static_server.port
    host = static_server.host
    static_server.shutdown()
    conn = HTTPConnection(host, port, timeout=1)
    with pytest.raises(OSError):
        conn.request("GET", ROUTE_HEALTH)
        conn.getresponse()
