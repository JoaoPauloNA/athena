"""Servidor concorrente de JSON-RPC 2.0 sobre stdio delimitado por linhas."""

from __future__ import annotations

import json
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from .contracts import MCPApplicationContract, PreparedToolCall, StdioTransport
from .modern import (
    LEGACY_PROTOCOL_VERSION,
    MAX_INPUT_LINE_BYTES,
    SERVER_NAME,
    SERVER_VERSION,
    SUPPORTED_MODERN_VERSIONS,
    ModernMetaError,
    discover_result,
    normalize_request_id,
    params_meta_kind,
    unsupported_version_error,
    validate_modern_meta,
    wrap_modern_call_result,
    wrap_modern_list_result,
    wrap_modern_success_result,
)

PROTOCOL_VERSION = LEGACY_PROTOCOL_VERSION
_MAX_CANCEL_REASON_LEN = 256


def _success(request_id: object, result: object) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(
    request_id: object,
    code: int,
    message: str,
    *,
    data: object = None,
) -> dict[str, object]:
    error: dict[str, object] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


class JsonRpcStdioServer:
    """Manter o loop de entrada responsivo enquanto tools demoradas executam."""

    def __init__(
        self,
        application: MCPApplicationContract,
        transport: StdioTransport,
        *,
        max_workers: int = 16,
    ) -> None:
        if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers <= 0:
            raise ValueError("max_workers must be a positive integer")
        self._application = application
        self._transport = transport
        self._max_workers = max_workers
        self._write_lock = threading.Lock()
        self._long_lock = threading.Lock()
        self._inflight_long: set[str | int] = set()
        self._cancelled_long: set[str | int] = set()

    def serve(self) -> None:
        executor = ThreadPoolExecutor(max_workers=self._max_workers)
        futures: set[Future[None]] = set()
        try:
            for raw_line in self._transport.stdin:
                if len(raw_line.encode("utf-8")) > MAX_INPUT_LINE_BYTES:
                    self._write(_error(None, -32700, "message too large"))
                    continue
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    self._write(_error(None, -32700, "invalid JSON"))
                    continue
                if not isinstance(message, dict):
                    self._write(_error(None, -32600, "invalid request"))
                    continue

                request_id = message.get("id")
                method = message.get("method")
                if request_id is None:
                    if (
                        isinstance(method, str)
                        and method == "notifications/cancelled"
                    ):
                        self._handle_cancelled(message)
                    continue

                normalized_id = normalize_request_id(request_id)
                if normalized_id is None:
                    self._write(_error(request_id, -32602, "invalid request id"))
                    continue

                preflight = self._prevalidate_request(message, normalized_id)
                if preflight is not None:
                    self._write(preflight)
                    continue

                params = message.get("params")
                name = params.get("name") if isinstance(params, dict) else None
                is_long = (
                    method == "tools/call"
                    and self._application.is_long_running(name)
                )
                if is_long:
                    with self._long_lock:
                        if normalized_id in self._inflight_long:
                            duplicate_long_id = True
                        else:
                            self._inflight_long.add(normalized_id)
                            duplicate_long_id = False
                    if duplicate_long_id:
                        self._write(
                            _error(request_id, -32602, "duplicate in-flight request id")
                        )
                        continue
                    try:
                        prepared = self._application.prepare_long_call(
                            str(name),
                            self._arguments(params),
                            request_id,
                        )
                    except (KeyError, TypeError, ValueError) as exc:
                        self._release_long_slot(normalized_id)
                        self._write(_error(request_id, -32602, str(exc)))
                        continue
                    except Exception as exc:  # noqa: BLE001 - preparation boundary
                        self._release_long_slot(normalized_id)
                        self._log_exception("long call preparation failed", exc)
                        self._write(_error(request_id, -32000, "internal MCP server error"))
                        continue
                    try:
                        future = executor.submit(
                            self._process, message, prepared, normalized_id
                        )
                    except Exception as exc:  # noqa: BLE001 - executor boundary
                        self._release_long_slot(normalized_id)
                        self._log_exception("long call submission failed", exc)
                        self._write(_error(request_id, -32000, "internal MCP server error"))
                        continue
                    futures.add(future)
                    futures = {item for item in futures if not item.done()}
                else:
                    self._process(message, None, None)
        finally:
            self._application.abandon_nonterminal()
            for future in tuple(futures):
                try:
                    future.result()
                except Exception as exc:  # noqa: BLE001  # pragma: no cover
                    self._log_exception("worker completion failed", exc)
            executor.shutdown(wait=True)

    def _prevalidate_request(
        self, message: dict[str, Any], normalized_id: str | int
    ) -> dict[str, object] | None:
        _ = normalized_id
        request_id = message.get("id")
        method = message.get("method")
        if message.get("jsonrpc") != "2.0" or not isinstance(method, str):
            return _error(request_id, -32600, "invalid request")

        params = message.get("params")
        meta_kind = params_meta_kind(params)
        if meta_kind == "partial":
            return _error(request_id, -32602, "invalid modern _meta")

        modern = meta_kind == "modern"
        if method == "initialize":
            if modern:
                return _error(request_id, -32600, "ambiguous request era")
            return None

        if modern:
            meta_error = self._validate_modern_request(request_id, params)
            if meta_error is not None:
                return meta_error

        if method == "server/discover":
            if not modern:
                return _error(request_id, -32601, "method not found: server/discover")
            return None

        if method == "tools/call":
            if not isinstance(params, dict):
                return _error(request_id, -32602, "params must be an object")
            name = params.get("name")
            if not isinstance(name, str):
                return _error(request_id, -32602, "tool name must be a string")
            try:
                self._arguments(params)
            except TypeError as exc:
                return _error(request_id, -32602, str(exc))

        return None

    def _process(
        self,
        message: dict[str, Any],
        prepared: PreparedToolCall | None,
        normalized_id: str | int | None,
    ) -> None:
        request_id = message.get("id")
        try:
            response = self._handle(message, prepared)
        except Exception as exc:  # noqa: BLE001  # pragma: no cover
            self._log_exception("request processing failed", exc)
            response = _error(request_id, -32000, "internal MCP server error")
        if normalized_id is None:
            if response is not None:
                self._write(response)
            return
        self._finish_long_response(normalized_id, response)

    def _finish_long_response(
        self, normalized_id: str | int, response: dict[str, object] | None
    ) -> None:
        # Lock order is always long -> write.  No path acquires _long_lock while
        # holding _write_lock, so cancellation and ordinary writes cannot deadlock.
        with self._long_lock:
            self._inflight_long.discard(normalized_id)
            cancelled = normalized_id in self._cancelled_long
            self._cancelled_long.discard(normalized_id)
            if response is not None and not cancelled:
                with self._write_lock:
                    self._write_locked(response)

    def _release_long_slot(self, normalized_id: str | int) -> None:
        with self._long_lock:
            self._inflight_long.discard(normalized_id)
            self._cancelled_long.discard(normalized_id)

    def _handle(
        self, message: dict[str, Any], prepared: PreparedToolCall | None
    ) -> dict[str, object] | None:
        request_id = message.get("id")
        method = message.get("method")
        params = message.get("params")
        modern = params_meta_kind(params) == "modern"

        if method == "initialize":
            return _success(
                request_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                },
            )
        if method == "notifications/initialized":
            return None

        if method == "server/discover":
            return _success(request_id, discover_result())

        if method == "ping":
            if modern:
                return _success(request_id, wrap_modern_success_result())
            return _success(request_id, {})

        if method == "tools/list":
            tools = list(self._application.tools)
            if modern:
                return _success(request_id, wrap_modern_list_result(tools))
            return _success(request_id, {"tools": tools})

        if method == "tools/call":
            name = params["name"] if isinstance(params, dict) else None
            try:
                result = self._application.call(
                    name,
                    self._arguments(params),
                    request_id=request_id,
                    prepared=prepared,
                )
            except LookupError as exc:
                return _error(request_id, -32601, str(exc))
            except (KeyError, TypeError, ValueError) as exc:
                return _error(request_id, -32602, str(exc))
            except Exception as exc:  # noqa: BLE001 - tool boundary containment
                self._log_exception(f"tool call failed:{name}", exc)
                return _error(request_id, -32000, f"tool failed: {name}")
            if modern:
                return _success(request_id, wrap_modern_call_result(result))
            return _success(request_id, result)

        return _error(request_id, -32601, f"method not found: {method}")

    def _validate_modern_request(
        self, request_id: object, params: dict[str, Any]
    ) -> dict[str, object] | None:
        meta = params.get("_meta")
        try:
            requested = validate_modern_meta(meta)
        except ModernMetaError as exc:
            return _error(request_id, -32602, str(exc))
        if requested not in SUPPORTED_MODERN_VERSIONS:
            error = unsupported_version_error(requested)
            return _error(
                request_id,
                int(error["code"]),  # type: ignore[arg-type]
                str(error["message"]),
                data=error.get("data"),
            )
        return None

    @staticmethod
    def _arguments(params: dict[str, Any]) -> dict[str, Any]:
        arguments = params.get("arguments", {})
        if arguments is None:
            return {}
        if not isinstance(arguments, dict):
            raise TypeError("arguments must be an object")
        return arguments

    def _handle_cancelled(self, message: dict[str, Any]) -> None:
        if message.get("jsonrpc") != "2.0":
            return
        if message.get("method") != "notifications/cancelled":
            return
        if message.get("id") is not None:
            return
        params = message.get("params")
        if not isinstance(params, dict):
            return
        normalized_id = normalize_request_id(params.get("requestId"))
        if normalized_id is None:
            return
        reason = params.get("reason")
        if reason is not None and (
            not isinstance(reason, str) or len(reason) > _MAX_CANCEL_REASON_LEN
        ):
            return
        cancel_reason = reason if isinstance(reason, str) else None
        should_cancel = False
        with self._long_lock:
            if normalized_id in self._inflight_long:
                self._cancelled_long.add(normalized_id)
                should_cancel = True
        if should_cancel:
            cancel_inflight = getattr(self._application, "cancel_inflight", None)
            if callable(cancel_inflight):
                cancel_inflight(normalized_id, reason=cancel_reason)

    def _write(self, response: dict[str, object]) -> None:
        with self._write_lock:
            self._write_locked(response)

    def _write_locked(self, response: dict[str, object]) -> None:
        line = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
        self._transport.stdout.write(line + "\n")
        self._transport.stdout.flush()

    def _log_exception(self, context: str, exc: Exception) -> None:
        self._transport.stderr.write(f"{context}: {type(exc).__name__}\n")
        self._transport.stderr.flush()
