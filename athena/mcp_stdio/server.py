"""Servidor concorrente de JSON-RPC 2.0 sobre stdio delimitado por linhas."""

from __future__ import annotations

import json
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from .contracts import MCPApplicationContract, PreparedToolCall, StdioTransport

PROTOCOL_VERSION = "2024-11-05"


def _success(request_id: object, result: object) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: object, code: int, message: str) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


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

    def serve(self) -> None:
        executor = ThreadPoolExecutor(max_workers=self._max_workers)
        futures: set[Future[None]] = set()
        try:
            for raw_line in self._transport.stdin:
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

                params = message.get("params")
                name = params.get("name") if isinstance(params, dict) else None
                is_long = (
                    message.get("method") == "tools/call"
                    and self._application.is_long_running(name)
                )
                if is_long:
                    request_id = message.get("id")
                    try:
                        arguments = self._arguments(params)
                        prepared = self._application.prepare_long_call(
                            str(name), arguments, request_id
                        )
                    except (KeyError, TypeError, ValueError) as exc:
                        self._write(_error(request_id, -32602, str(exc)))
                        continue
                    future = executor.submit(self._process, message, prepared)
                    futures.add(future)
                    futures = {item for item in futures if not item.done()}
                else:
                    self._process(message, None)
        finally:
            self._application.abandon_nonterminal()
            for future in tuple(futures):
                try:
                    future.result()
                except Exception as exc:  # noqa: BLE001  # pragma: no cover
                    self._log_exception("worker completion failed", exc)
            executor.shutdown(wait=True)

    def _process(
        self, message: dict[str, Any], prepared: PreparedToolCall | None
    ) -> None:
        request_id = message.get("id")
        try:
            response = self._handle(message, prepared)
        except Exception as exc:  # noqa: BLE001  # pragma: no cover
            self._log_exception("request processing failed", exc)
            response = _error(request_id, -32000, "internal MCP server error")
        if response is not None:
            self._write(response)

    def _handle(
        self, message: dict[str, Any], prepared: PreparedToolCall | None
    ) -> dict[str, object] | None:
        request_id = message.get("id")
        method = message.get("method")
        if message.get("jsonrpc") != "2.0" or not isinstance(method, str):
            return _error(request_id, -32600, "invalid request")
        if method == "initialize":
            return _success(
                request_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "athena-mcp", "version": "0.0.0"},
                },
            )
        if method == "notifications/initialized":
            return None
        if method == "ping":
            return _success(request_id, {})
        if method == "tools/list":
            return _success(request_id, {"tools": list(self._application.tools)})
        if method == "tools/call":
            params = message.get("params")
            if not isinstance(params, dict):
                return _error(request_id, -32602, "params must be an object")
            name = params.get("name")
            if not isinstance(name, str):
                return _error(request_id, -32602, "tool name must be a string")
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
            return _success(request_id, result)
        if request_id is None:
            return None
        return _error(request_id, -32601, f"method not found: {method}")

    @staticmethod
    def _arguments(params: dict[str, Any]) -> dict[str, Any]:
        arguments = params.get("arguments", {})
        if arguments is None:
            return {}
        if not isinstance(arguments, dict):
            raise TypeError("arguments must be an object")
        return arguments

    def _write(self, response: dict[str, object]) -> None:
        line = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
        with self._write_lock:
            self._transport.stdout.write(line + "\n")
            self._transport.stdout.flush()

    def _log_exception(self, context: str, exc: Exception) -> None:
        self._transport.stderr.write(f"{context}: {type(exc).__name__}\n")
        self._transport.stderr.flush()
