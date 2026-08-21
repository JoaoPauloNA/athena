"""Adaptação dos argumentos JSON para os handlers do MCPServer modular."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from aegis.contracts import FailureCondition

from athena.bridge import RunRequest
from athena.execution import ExecutionDeadlines
from athena.mcp_server import MCPServerContract, PreparedExecution
from athena.router import ComboAttempt, ComboRequest
from athena.verifier import CommandClaim, FileClaim, VerificationRequest

from .contracts import PreparedToolCall

LONG_RUNNING_TOOLS = frozenset({"run_combo", "ask_provider"})

TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "run_combo",
        "description": "Executa uma sequência modular de tentativas com fallback governado.",
        "inputSchema": {
            "type": "object",
            "required": ["attempts"],
            "properties": {
                "attempts": {"type": "array", "minItems": 1},
                "profile": {"type": ["string", "null"]},
                "overall_timeout_s": {"type": ["number", "null"], "exclusiveMinimum": 0},
                "execution_id": {"type": "string", "minLength": 1},
                "verification": {"type": "object"},
            },
        },
    },
    {
        "name": "ask_provider",
        "description": "Executa uma solicitação modular preparada para um provider.",
        "inputSchema": {
            "type": "object",
            "required": ["provider_id", "attempts"],
            "properties": {
                "provider_id": {"type": "string", "minLength": 1},
                "attempts": {"type": "array", "minItems": 1},
                "profile": {"type": ["string", "null"]},
                "task_type": {},
                "working_directory": {"type": ["string", "null"]},
                "overall_timeout_s": {"type": ["number", "null"], "exclusiveMinimum": 0},
                "execution_id": {"type": "string", "minLength": 1},
                "verification": {"type": "object"},
            },
        },
    },
    {
        "name": "get_execution",
        "description": "Consulta uma execução sanitizada por execution_id ou request_id.",
        "inputSchema": {
            "type": "object",
            "properties": {"execution_id": {"type": "string"}, "request_id": {}},
        },
    },
    {
        "name": "list_executions",
        "description": "Lista execuções sanitizadas recentes.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1}},
        },
    },
    {
        "name": "cancel_execution",
        "description": "Solicita cancelamento idempotente por execution_id ou request_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "execution_id": {"type": "string"},
                "request_id": {},
                "reason": {"type": ["string", "null"]},
            },
        },
    },
)


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be an object")
    return value


def _sequence(value: object, name: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(f"{name} must be an array")
    return value


def _combo(arguments: Mapping[str, Any], execution_id: str | None) -> ComboRequest:
    raw_attempts = _sequence(arguments["attempts"], "attempts")
    attempts: list[ComboAttempt] = []
    for index, value in enumerate(raw_attempts):
        item = _mapping(value, f"attempts[{index}]")
        command = _sequence(item["command"], f"attempts[{index}].command")
        if not command or not all(isinstance(part, str) and part for part in command):
            raise ValueError(f"attempts[{index}].command must contain strings")
        env = _mapping(item.get("env", {}), f"attempts[{index}].env")
        if not all(isinstance(key, str) and isinstance(val, str) for key, val in env.items()):
            raise TypeError(f"attempts[{index}].env must contain strings")
        deadlines = _mapping(item.get("deadlines", {}), f"attempts[{index}].deadlines")
        failure = FailureCondition(item.get("failure_condition", "provider_error"))
        attempts.append(
            ComboAttempt(
                provider=item["provider"],
                request=RunRequest(
                    tuple(command),
                    item["cwd"],
                    env=dict(env),
                    use_pty=item.get("use_pty", False),
                    lease_timeout_s=item.get("lease_timeout_s"),
                    termination_grace_s=item.get("termination_grace_s", 0.5),
                ),
                deadlines=ExecutionDeadlines(
                    absolute_timeout_s=deadlines.get("absolute_timeout_s"),
                    idle_timeout_s=deadlines.get("idle_timeout_s"),
                ),
                failure_condition=failure,
            )
        )
    return ComboRequest(
        attempts=attempts,
        profile=arguments.get("profile"),
        overall_timeout_s=arguments.get("overall_timeout_s"),
        execution_id=execution_id,
    )


def _verification(value: object) -> VerificationRequest | None:
    if value is None:
        return None
    item = _mapping(value, "verification")
    files = tuple(FileClaim(path) for path in _sequence(item.get("files", ()), "verification.files"))
    commands = tuple(
        CommandClaim(tuple(_sequence(command, "verification.commands[]")))
        for command in _sequence(item.get("commands", ()), "verification.commands")
    )
    return VerificationRequest(
        files=files,
        commands=commands,
        working_directory=item.get("working_directory"),
        repository_root=item.get("repository_root"),
    )


class MCPApplication:
    """Converter JSON inerte e invocar somente a superfície modular atual."""

    def __init__(self, server: MCPServerContract) -> None:
        self._server = server

    @property
    def tools(self) -> tuple[dict[str, Any], ...]:
        return TOOLS

    def is_long_running(self, name: object) -> bool:
        return name in LONG_RUNNING_TOOLS

    def prepare_long_call(
        self,
        name: str,
        arguments: Mapping[str, Any],
        request_id: object,
    ) -> PreparedToolCall:
        if isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
            raise TypeError("long-running request id must be a string or integer")
        if isinstance(request_id, str) and not request_id:
            raise ValueError("long-running request id must not be empty")
        provided_id = arguments.get("execution_id")
        if provided_id is not None and (
            not isinstance(provided_id, str) or not provided_id.strip()
        ):
            raise ValueError("execution_id must be a non-empty string")
        _combo(arguments, provided_id if isinstance(provided_id, str) else None)
        _verification(arguments.get("verification"))
        if name == "ask_provider":
            provider_id = arguments["provider_id"]
            if not isinstance(provider_id, str) or not provider_id.strip():
                raise ValueError("provider_id must be a non-empty string")
        reservation = self._server.prepare_execution(
            name,
            request_id=request_id,
            execution_id=provided_id,
        )
        prepared_arguments = dict(arguments)
        prepared_arguments["execution_id"] = reservation.execution_id
        return PreparedToolCall(name, prepared_arguments, request_id, reservation)

    def call(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        request_id: object,
        prepared: PreparedToolCall | None = None,
    ) -> Mapping[str, Any]:
        if name == "run_combo" or name == "ask_provider":
            if isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
                raise ValueError("long-running request id must be a string or integer")
            reservation = None
            if prepared is not None:
                if prepared.name != name or prepared.request_id != request_id:
                    raise ValueError("prepared call does not match request")
                arguments = prepared.arguments
                if not isinstance(prepared.reservation, PreparedExecution):
                    raise TypeError("invalid execution reservation")
                reservation = prepared.reservation
            execution_id = arguments.get("execution_id")
            combo = _combo(arguments, execution_id if isinstance(execution_id, str) else None)
            verification = _verification(arguments.get("verification"))
            if name == "run_combo":
                payload = self._server.run_combo(
                    combo,
                    request_id=request_id,
                    verification=verification,
                    prepared=reservation,
                )
            else:
                payload = self._server.ask_provider(
                    combo,
                    request_id=request_id,
                    provider_id=arguments["provider_id"],
                    task_type=arguments.get("task_type"),
                    working_directory=arguments.get("working_directory"),
                    verification=verification,
                    prepared=reservation,
                )
        elif name == "get_execution":
            payload = self._server.get_execution(
                arguments.get("execution_id"), request_id=arguments.get("request_id")
            )
        elif name == "list_executions":
            payload = self._server.list_executions(limit=arguments.get("limit"))
        elif name == "cancel_execution":
            payload = self._server.cancel_execution(
                arguments.get("execution_id"),
                request_id=arguments.get("request_id"),
                reason=arguments.get("reason"),
            )
        else:
            raise LookupError(f"unknown tool: {name}")
        return {
            "content": [
                {"type": "text", "text": json.dumps(payload, ensure_ascii=False)}
            ]
        }

    def abandon_nonterminal(self) -> int:
        return self._server.abandon_nonterminal(reason="client_abandoned")
