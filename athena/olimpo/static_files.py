"""Serviço estático confinado para o frontend O-1 em loopback."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path

from .contracts import (
    LOOPBACK_HOST,
    STABLE_REASON_CODES,
    OlimpoError,
    validate_csrf_token,
)
from .sanitize import dumps_response, error_payload

MAX_STATIC_BYTES = 8 * 1024 * 1024
_INDEX_NAME = "index.html"
_HASHED_ASSET_RE = re.compile(r"[-.][0-9A-Za-z]{8,}\.(?:js|css|woff2?)$")

# React (O-1) usa atributos style inline no bundle local; permitido só em loopback.
_STYLE_UNSAFE_INLINE_REASON = (
    "React UI local usa style= inline; restrito a style-src 'self' 'unsafe-inline'."
)

_ALLOWED_MIME = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".ico": "image/x-icon",
    ".js": "application/javascript; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}

_JSON_CSP = "default-src 'none'; frame-ancestors 'none'"


@dataclass(frozen=True, slots=True)
class StaticResponse:
    status: int
    body: bytes
    content_type: str
    headers: dict[str, str]


def _html_csp(nonce: str) -> str:
    return (
        "default-src 'self'; "
        "base-uri 'none'; "
        "form-action 'none'; "
        "frame-ancestors 'none'; "
        "object-src 'none'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        "style-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; "
        "img-src 'self' data:; "
        "font-src 'self'"
    )


def generate_csp_nonce() -> str:
    return secrets.token_urlsafe(16)


def inject_csrf_bootstrap(html: bytes, token: str, *, nonce: str) -> bytes:
    """Injeta CSRF somente na resposta HTML em memória — nunca persiste."""
    safe_token = validate_csrf_token(token)
    script = (
        f'<script nonce="{nonce}">window.__OLIMPO_CSRF_TOKEN__='
        + json.dumps(safe_token, ensure_ascii=True)
        + ";</script>"
    ).encode("utf-8")
    marker = b"</head>"
    if marker in html:
        return html.replace(marker, script + marker, 1)
    return script + html


def _cache_control_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if path.name == _INDEX_NAME:
        return "no-store"
    if suffix in {".js", ".css"} and _HASHED_ASSET_RE.search(path.name):
        return "public, max-age=31536000, immutable"
    if suffix in {".js", ".css", ".woff", ".woff2", ".png", ".svg", ".ico"}:
        return "public, max-age=3600"
    return "no-store"


def _assert_static_root_safe(root: Path) -> None:
    if root.is_symlink():
        raise OlimpoError("OLIMPO_STATIC_FORBIDDEN", status=403)
    if not root.is_dir():
        raise OlimpoError("OLIMPO_STATIC_NOT_FOUND", status=404)


def _resolve_static_path(root: Path, request_path: str) -> Path:
    if not request_path or request_path[0] != "/":
        raise OlimpoError("OLIMPO_STATIC_FORBIDDEN", status=403)
    if "\x00" in request_path:
        raise OlimpoError("OLIMPO_STATIC_FORBIDDEN", status=403)
    relative = request_path.lstrip("/")
    if not relative:
        relative = _INDEX_NAME
    if relative.startswith("/") or ".." in relative.split("/"):
        raise OlimpoError("OLIMPO_STATIC_FORBIDDEN", status=403)

    current = root
    for part in relative.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            raise OlimpoError("OLIMPO_STATIC_FORBIDDEN", status=403)
        current = current / part
        if current.is_symlink():
            raise OlimpoError("OLIMPO_STATIC_FORBIDDEN", status=403)

    root_resolved = root.resolve(strict=False)
    candidate = current.resolve(strict=False)
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise OlimpoError("OLIMPO_STATIC_FORBIDDEN", status=403) from exc
    return candidate


def _read_regular_file(path: Path, *, max_bytes: int) -> bytes:
    if path.is_symlink():
        raise OlimpoError("OLIMPO_STATIC_FORBIDDEN", status=403)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        stat_result = os.fstat(fd)
        if not stat.S_ISREG(stat_result.st_mode):
            raise OlimpoError("OLIMPO_STATIC_FORBIDDEN", status=403)
        if stat_result.st_size > max_bytes:
            raise OlimpoError("OLIMPO_STATIC_TOO_LARGE", status=413)
        chunks: list[bytes] = []
        remaining = stat_result.st_size
        while remaining > 0:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def serve_static(
    root: Path,
    request_path: str,
    *,
    csrf_token: str = "",
    head_only: bool = False,
) -> StaticResponse:
    """Serve um arquivo estático allowlisted com contenção e headers de segurança."""
    _assert_static_root_safe(root)

    try:
        candidate = _resolve_static_path(root, request_path)
    except OlimpoError:
        raise
    except OSError as exc:
        raise OlimpoError("OLIMPO_STATIC_NOT_FOUND", status=404) from exc

    if not candidate.exists():
        raise OlimpoError("OLIMPO_STATIC_NOT_FOUND", status=404)

    suffix = candidate.suffix.lower()
    content_type = _ALLOWED_MIME.get(suffix)
    if content_type is None:
        raise OlimpoError("OLIMPO_STATIC_FORBIDDEN", status=403)

    payload = _read_regular_file(candidate, max_bytes=MAX_STATIC_BYTES)
    headers = dict(_SECURITY_HEADERS)
    if suffix == ".html":
        nonce = generate_csp_nonce()
        if candidate.name == _INDEX_NAME:
            payload = inject_csrf_bootstrap(payload, csrf_token, nonce=nonce)
        headers["Content-Security-Policy"] = _html_csp(nonce)
    else:
        headers["Content-Security-Policy"] = _JSON_CSP
    headers["Cache-Control"] = _cache_control_for(candidate)
    body = b"" if head_only else payload
    return StaticResponse(
        status=200,
        body=body,
        content_type=content_type,
        headers=headers,
    )


def api_response(
    status: int,
    body: bytes,
    *,
    content_type: str = "application/json",
) -> StaticResponse:
    headers = dict(_SECURITY_HEADERS)
    headers["Content-Security-Policy"] = _JSON_CSP
    headers["Cache-Control"] = "no-store"
    return StaticResponse(
        status=status,
        body=body,
        content_type=content_type,
        headers=headers,
    )


def error_response(reason_code: str, *, status: int = 400) -> StaticResponse:
    if reason_code not in STABLE_REASON_CODES:
        reason_code = "OLIMPO_INTERNAL_ERROR"
        status = 500
    return api_response(status, dumps_response(error_payload(reason_code)))


def snapshot_identity(path: Path) -> str | None:
    """Hash estável do diretório estático configurado (somente metadados públicos)."""
    if not path.is_dir() or path.is_symlink():
        return None
    digest = hashlib.sha256()
    for entry in sorted(path.rglob("*")):
        if not entry.is_file() or entry.is_symlink():
            continue
        rel = entry.relative_to(path).as_posix().encode("utf-8")
        stat_result = entry.stat()
        digest.update(rel)
        digest.update(str(stat_result.st_size).encode("utf-8"))
        digest.update(str(int(stat_result.st_mtime_ns)).encode("utf-8"))
    return digest.hexdigest()


def same_origin(base_host: str, port: int) -> str:
    if base_host != LOOPBACK_HOST:
        raise OlimpoError("OLIMPO_BIND_FORBIDDEN", status=500)
    return f"http://{base_host}:{port}"


def style_unsafe_inline_reason() -> str:
    return _STYLE_UNSAFE_INLINE_REASON
