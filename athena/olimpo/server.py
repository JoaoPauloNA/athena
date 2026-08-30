"""Servidor HTTP opt-in em loopback para O-0/O-2."""

from __future__ import annotations

import secrets
import socket
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .app import OlimpoApp
from .contracts import (
    CSRF_HEADER,
    LOOPBACK_HOST,
    MAX_REQUEST_BYTES,
    OlimpoDependencies,
    OlimpoError,
    validate_allowed_origins,
    validate_csrf_token,
    validate_loopback_origin,
)
from .sanitize import dumps_response, error_payload
from .static_files import StaticResponse, api_response, same_origin

_CRITICAL_SINGLE_VALUE_HEADERS = frozenset(
    {
        "origin",
        CSRF_HEADER.lower(),
        "content-length",
        "content-type",
        "transfer-encoding",
    }
)

def _normalize_headers(headers: Any) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in headers.items():
        normalized[key.lower()] = value
    return normalized


def _header_error(reason_code: str, *, status: int = 400) -> StaticResponse:
    return api_response(status, dumps_response(error_payload(reason_code)))


def _validate_single_value_headers(headers: Any) -> StaticResponse | None:
    for name in _CRITICAL_SINGLE_VALUE_HEADERS:
        values = headers.get_all(name)
        if values is None:
            continue
        if len(values) > 1:
            return _header_error("OLIMPO_FIELD_INVALID")
    return None


def _read_post_body(headers: Any, rfile: Any) -> tuple[bytes | None, StaticResponse | None]:
    transfer_values = headers.get_all("Transfer-Encoding")
    if transfer_values:
        return None, _header_error("OLIMPO_FIELD_INVALID")

    content_length_values = headers.get_all("Content-Length")
    if content_length_values is None or len(content_length_values) == 0:
        return None, _header_error("OLIMPO_FIELD_INVALID")
    if len(content_length_values) > 1:
        return None, _header_error("OLIMPO_FIELD_INVALID")

    raw_length = content_length_values[0].strip()
    if not raw_length:
        return None, _header_error("OLIMPO_FIELD_INVALID")
    try:
        length = int(raw_length, 10)
    except ValueError:
        return None, _header_error("OLIMPO_FIELD_INVALID")
    if length < 0:
        return None, _header_error("OLIMPO_FIELD_INVALID")
    if length > MAX_REQUEST_BYTES:
        return None, _header_error("OLIMPO_REQUEST_TOO_LARGE", status=413)

    body = rfile.read(length) if length > 0 else b""
    return body, None


def _write_response(handler: BaseHTTPRequestHandler, response: StaticResponse) -> None:
    handler.send_response(response.status)
    handler.send_header("Content-Type", response.content_type)
    handler.send_header("Content-Length", str(len(response.body)))
    for name, value in response.headers.items():
        handler.send_header(name, value)
    handler.end_headers()
    if response.body:
        handler.wfile.write(response.body)


class OlimpoHttpServer:
    """Adapter HTTP local — só inicia quando ``start()`` é chamado explicitamente."""

    def __init__(
        self,
        dependencies: OlimpoDependencies,
        *,
        host: str = LOOPBACK_HOST,
        port: int = 0,
        static_root: Path | None = None,
    ) -> None:
        if host != LOOPBACK_HOST:
            raise OlimpoError("OLIMPO_BIND_FORBIDDEN", status=500)
        if not dependencies.csrf_token:
            dependencies = replace(
                dependencies, csrf_token=validate_csrf_token(secrets.token_hex(32))
            )
        else:
            dependencies = replace(
                dependencies,
                csrf_token=validate_csrf_token(dependencies.csrf_token),
            )
        dependencies = replace(
            dependencies,
            allowed_origins=validate_allowed_origins(dependencies.allowed_origins),
        )
        if static_root is not None:
            if static_root.is_symlink():
                raise OlimpoError("OLIMPO_STATIC_FORBIDDEN", status=403)
            dependencies = replace(dependencies, static_root=static_root)
        self._deps = dependencies
        self._app = OlimpoApp(dependencies)
        self._host = host
        self._port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def csrf_token(self) -> str:
        return self._deps.csrf_token

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        if self._httpd is None:
            return self._port
        return int(self._httpd.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> None:
        if self._httpd is not None:
            return
        handler = self._build_handler()
        self._httpd = ThreadingHTTPServer((self._host, self._port), handler)
        self._httpd.daemon_threads = True
        self._port = int(self._httpd.server_address[1])
        origin = validate_loopback_origin(same_origin(self._host, self._port))
        self._deps = replace(
            self._deps,
            allowed_origins=self._deps.allowed_origins | frozenset({origin}),
        )
        self._app = OlimpoApp(self._deps)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="olimpo-http",
            daemon=True,
        )
        self._thread.start()

    def shutdown(self) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def handle_request(
        self,
        *,
        method: str,
        path: str,
        query: str = "",
        headers: dict[str, str] | None = None,
        body: bytes = b"",
        client_host: str = LOOPBACK_HOST,
    ) -> tuple[int, bytes]:
        parsed = urlparse(path)
        resolved_query = query or parsed.query
        response = self._app.handle(
            method=method.upper(),
            path=parsed.path or path,
            query=resolved_query,
            headers=_normalize_headers(headers or {}),
            body=body,
            client_host=client_host,
        )
        return response.status, response.body

    def handle_response(
        self,
        *,
        method: str,
        path: str,
        query: str = "",
        headers: dict[str, str] | None = None,
        body: bytes = b"",
        client_host: str = LOOPBACK_HOST,
    ) -> StaticResponse:
        parsed = urlparse(path)
        resolved_query = query or parsed.query
        return self._app.handle(
            method=method.upper(),
            path=parsed.path or path,
            query=resolved_query,
            headers=_normalize_headers(headers or {}),
            body=body,
            client_host=client_host,
        )

    def _build_handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "OlimpoO0/0"
            sys_version = ""

            def log_message(self, format: str, *args: object) -> None:
                return

            def _dispatch(self, method: str) -> None:
                header_error = _validate_single_value_headers(self.headers)
                if header_error is not None:
                    _write_response(self, header_error)
                    return
                if method == "POST":
                    body, body_error = _read_post_body(self.headers, self.rfile)
                    if body_error is not None:
                        _write_response(self, body_error)
                        return
                    response = server._app.handle(
                        method=method,
                        path=urlparse(self.path).path,
                        query=urlparse(self.path).query,
                        headers=_normalize_headers(self.headers),
                        body=body or b"",
                        client_host=self.client_address[0],
                    )
                else:
                    response = server._app.handle(
                        method=method,
                        path=urlparse(self.path).path,
                        query=urlparse(self.path).query,
                        headers=_normalize_headers(self.headers),
                        body=b"",
                        client_host=self.client_address[0],
                    )
                _write_response(self, response)

            def do_GET(self) -> None:
                self._dispatch("GET")

            def do_HEAD(self) -> None:
                self._dispatch("HEAD")

            def do_POST(self) -> None:
                self._dispatch("POST")

            def _method_not_allowed(self) -> None:
                response = api_response(
                    405,
                    dumps_response(error_payload("OLIMPO_METHOD_NOT_ALLOWED")),
                )
                _write_response(self, response)

            def do_PUT(self) -> None:
                self._method_not_allowed()

            def do_DELETE(self) -> None:
                self._method_not_allowed()

            def do_PATCH(self) -> None:
                self._method_not_allowed()

            def do_OPTIONS(self) -> None:
                self._method_not_allowed()

        return Handler


def assert_loopback_host(host: str) -> None:
    if host != LOOPBACK_HOST:
        raise OlimpoError("OLIMPO_BIND_FORBIDDEN", status=500)


def is_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((host, port)) == 0
