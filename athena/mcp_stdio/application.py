"""Adaptação dos argumentos JSON para os handlers do MCPServer modular."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from aegis import canonical_digest
from aegis.contracts import FailureCondition

from athena.bridge import RunRequest
from athena.execution import ExecutionDeadlines
from athena.mcp_server import MCPServerContract, PreparedExecution
from athena.router import (
    AllAttemptsFailed,
    ComboAttempt,
    ComboRequest,
    RoutingContext,
)
from athena.tasks import (
    TaskHandleNotFound,
    TaskIdempotencyConflict,
    TaskNotExecutable,
    TaskStoreUnavailable,
    build_submission,
)
from athena.verifier import CommandClaim, FileClaim, VerificationRequest

from .contracts import PreparedToolCall

LONG_RUNNING_TOOLS = frozenset({"run_combo", "ask_provider"})
_ROUTE_REQUIRED = (
    "task_type",
    "primary_domain",
    "risk_level",
    "required_capabilities",
)
_ROUTE_TOKEN = re.compile(r"[a-z][a-z0-9_.:-]{0,127}")
_ROUTE_RISKS = ("low", "medium", "high", "critical")
_ROUTE_PROPERTIES = {
    "task_type": {"type": "string", "pattern": "^[a-z][a-z0-9_.:-]{0,127}$"},
    "primary_domain": {"type": "string", "pattern": "^[a-z][a-z0-9_.:-]{0,127}$"},
    "risk_level": {"type": "string", "enum": list(_ROUTE_RISKS)},
    "required_capabilities": {
        "type": "array",
        "minItems": 1,
        "maxItems": 64,
        "uniqueItems": True,
        "items": {"type": "string", "pattern": "^[a-z][a-z0-9_.:-]{0,127}$"},
    },
    "explicit_agent_tag": {
        "type": "string",
        "pattern": "^[a-z][a-z0-9_.:-]{0,127}$",
    },
}

TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "run_combo",
        "description": "Executa uma sequência modular de tentativas com fallback governado.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["attempts", *_ROUTE_REQUIRED],
            "properties": {
                "attempts": {"type": "array", "minItems": 1},
                "profile": {"type": "string"},
                "overall_timeout_s": {"type": "number", "exclusiveMinimum": 0},
                "execution_id": {"type": "string", "minLength": 1},
                "verification": {"type": "object"},
                "task_handle": {"type": "string", "minLength": 1},
                **_ROUTE_PROPERTIES,
            },
        },
    },
    {
        "name": "ask_provider",
        "description": "Executa uma solicitação modular preparada para um provider.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["provider_id", "attempts", *_ROUTE_REQUIRED],
            "properties": {
                "provider_id": {"type": "string", "minLength": 1},
                "attempts": {"type": "array", "minItems": 1},
                "profile": {"type": "string"},
                "task_type": {},
                "working_directory": {"type": ["string", "null"]},
                "overall_timeout_s": {"type": "number", "exclusiveMinimum": 0},
                "execution_id": {"type": "string", "minLength": 1},
                "verification": {"type": "object"},
                "task_handle": {"type": "string", "minLength": 1},
                **_ROUTE_PROPERTIES,
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
    {
        "name": "submit_task",
        "description": (
            "Submete uma tarefa durável e idempotente. Não a executa; apenas "
            "persiste o handle e o estado enfileirado."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["idempotency_key", "task"],
            "properties": {
                "idempotency_key": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 256,
                    "description": (
                        "Limite anunciado em caracteres; o runtime aplica o "
                        "limite autoritativo de 256 bytes UTF-8."
                    ),
                },
                "task": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["task_type", "input"],
                    "properties": {
                        "task_type": {
                            "type": "string",
                            "pattern": "^[a-z][a-z0-9_.-]{0,127}$",
                            "maxLength": 128,
                        },
                        "input": {
                            "type": "string",
                            "maxLength": 32768,
                            "description": (
                                "Limite anunciado em caracteres; o runtime "
                                "aplica o limite autoritativo de 32 KiB UTF-8."
                            ),
                        },
                        "project_ref": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 1024,
                            "description": (
                                "Limite anunciado em caracteres; o runtime "
                                "aplica o limite autoritativo de 1024 bytes UTF-8."
                            ),
                        },
                        "constraints": {"type": "object"},
                        "expected_output": {"type": "object"},
                        "priority": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 9,
                            "default": 5,
                        },
                    },
                },
            },
        },
    },
    {
        "name": "get_task",
        "description": "Consulta a projeção sanitizada de uma tarefa durável pelo handle.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["task_handle"],
            "properties": {"task_handle": {"type": "string", "minLength": 1}},
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
        tests=(canonical_digest(arguments["verification"]),)
        if arguments.get("verification") is not None
        else (),
    )


def _routing_context(arguments: Mapping[str, Any]) -> RoutingContext | None:
    present = tuple(name in arguments for name in _ROUTE_REQUIRED)
    if not any(present):
        return None
    if not all(present):
        raise ValueError("ROUTE_CONTEXT_MISSING")
    task_type = arguments["task_type"]
    primary_domain = arguments["primary_domain"]
    risk_level = arguments["risk_level"]
    raw_capabilities = arguments["required_capabilities"]
    explicit_tag = arguments.get("explicit_agent_tag")
    if (
        not isinstance(task_type, str)
        or _ROUTE_TOKEN.fullmatch(task_type) is None
        or not isinstance(primary_domain, str)
        or _ROUTE_TOKEN.fullmatch(primary_domain) is None
        or risk_level not in _ROUTE_RISKS
        or (explicit_tag is not None and (
            not isinstance(explicit_tag, str)
            or _ROUTE_TOKEN.fullmatch(explicit_tag) is None
        ))
    ):
        raise ValueError("ROUTE_CONTEXT_INVALID")
    capabilities = _sequence(raw_capabilities, "ROUTE_CONTEXT_INVALID")
    if (
        not 1 <= len(capabilities) <= 64
        or any(
            not isinstance(capability, str)
            or _ROUTE_TOKEN.fullmatch(capability) is None
            for capability in capabilities
        )
        or len(set(capabilities)) != len(capabilities)
    ):
        raise ValueError("ROUTE_CONTEXT_INVALID")
    return RoutingContext(
        task_type=task_type,
        primary_domain=primary_domain,
        risk_level=risk_level,
        required_capabilities=tuple(sorted(capabilities)),
        explicit_agent_tag=explicit_tag,
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


def _result_payload(result: object | None, *, error: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for field in (
        "state",
        "exit_code",
        "stdout",
        "stderr",
        "duration_s",
        "expired_deadline",
        "error",
    ):
        value = getattr(result, field, None)
        if hasattr(value, "value"):
            value = value.value
        payload[field] = value
    if result is None:
        payload["error"] = error
    return payload


def _tool_result(payload: Mapping[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "content": [
            {"type": "text", "text": json.dumps(payload, ensure_ascii=False)}
        ]
    }
    if is_error:
        result["isError"] = True
    return result


def _validate_flow_handle(
    arguments: Mapping[str, Any], verification: Any
) -> str | None:
    """Validate task_handle + verification preconditions for FLOW-1.

    Returns the validated task_handle string, or None if absent.
    Raises ValueError with stable code on any precondition failure.
    """
    raw = arguments.get("task_handle")
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("TASK_HANDLE_INVALID")
    # verification with at least one file or command claim is mandatory
    if verification is None or (
        not getattr(verification, "files", None)
        and not getattr(verification, "commands", None)
    ):
        raise ValueError("FLOW_VERIFICATION_MISSING")
    return raw


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
        _routing_context(arguments)
        verification = _verification(arguments.get("verification"))
        # Correction 5: validate task_handle + verification BEFORE reserving execution
        _validate_flow_handle(arguments, verification)
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
            routing_context = _routing_context(arguments)
            verification = _verification(arguments.get("verification"))
            # FLOW-1: validate task_handle + verification using shared helper
            try:
                task_handle = _validate_flow_handle(arguments, verification)
            except ValueError as exc:
                return _tool_result({"error": str(exc)}, is_error=True)
            try:
                if name == "run_combo":
                    payload = self._server.run_combo(
                        combo,
                        request_id=request_id,
                        verification=verification,
                        prepared=reservation,
                        routing_context=routing_context,
                        task_handle=task_handle,
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
                        routing_context=routing_context,
                        task_handle=task_handle,
                    )
            except AllAttemptsFailed as exc:
                return _tool_result(
                    {
                        "execution_id": combo.execution_id,
                        "result": _result_payload(
                            exc.last_result,
                            error=str(exc),
                        ),
                    },
                    is_error=True,
                )
            except TaskHandleNotFound:
                # Correction 4: stable reason_code, no handle fragment
                return _tool_result({"error": "TASK_HANDLE_NOT_FOUND"}, is_error=True)
            except TaskNotExecutable as exc:
                return _tool_result({"error": exc.reason_code}, is_error=True)
            except TaskStoreUnavailable as exc:
                msg = str(exc)
                code = (
                    "FLOW_CONTROLLER_UNAVAILABLE"
                    if "FLOW_CONTROLLER_UNAVAILABLE" in msg
                    else "TASK_STORE_UNAVAILABLE"
                )
                return _tool_result({"error": code}, is_error=True)
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
        elif name == "submit_task":
            submission = build_submission(
                arguments.get("idempotency_key"), arguments.get("task")
            )
            try:
                payload = self._server.submit_task(submission)
            except TaskIdempotencyConflict:
                return _tool_result({"error": "IDEMPOTENCY_CONFLICT"}, is_error=True)
            except TaskStoreUnavailable:
                return _tool_result({"error": "TASK_STORE_UNAVAILABLE"}, is_error=True)
        elif name == "get_task":
            task_handle = arguments.get("task_handle")
            if not isinstance(task_handle, str) or not task_handle:
                raise ValueError("task_handle must be a non-empty string")
            try:
                payload = self._server.get_task(task_handle)
            except TaskStoreUnavailable:
                return _tool_result({"error": "TASK_STORE_UNAVAILABLE"}, is_error=True)
        else:
            raise LookupError(f"unknown tool: {name}")
        return _tool_result(payload)

    def abandon_nonterminal(self) -> int:
        return self._server.abandon_nonterminal(reason="client_abandoned")

    def cancel_inflight(self, request_id: object, *, reason: str | None = None) -> None:
        """Cancel a long-running execution keyed by JSON-RPC request id."""
        if isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
            return
        self._server.cancel_execution(request_id=request_id, reason=reason)
