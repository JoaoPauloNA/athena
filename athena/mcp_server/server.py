"""Implementacao fina e sem transporte das tools MCP."""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Any

from athena.execution import ExecutionDeadlines, ExecutionRecord, ExecutionState
from athena.registry import RequestId
from athena.router import AllAttemptsFailed, ComboRequest
from athena.verifier import VerificationRequest, VerificationResult

from .contracts import (
    MCPServerContract,
    MCPServerDependencies,
    PreparedExecution,
    ToolPayload,
)

TOOL_NAMES = (
    "run_combo",
    "ask_provider",
    "get_execution",
    "list_executions",
    "cancel_execution",
)


def _result_payload(result: Any) -> dict[str, Any]:
    """Projetar o resultado do router sem conhecer implementacoes inferiores."""
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
    return payload


def _attempt_snapshot(result: Any) -> dict[str, Any]:
    payload = _result_payload(result)
    return {
        "state": payload["state"],
        "exit_code": payload["exit_code"],
        "duration_ms": round(float(payload["duration_s"] or 0.0) * 1000),
        "reason": payload["expired_deadline"] or payload["error"],
    }


def _verification_payload(result: VerificationResult) -> dict[str, Any]:
    return {
        "accepted": result.accepted,
        "deterministic": dict(result.deterministic.execution),
        "advisory": dict(result.advisory.execution),
    }


def _record_for_shadow(execution_id: str, result: Any) -> ExecutionRecord:
    """Reconstruir um record mínimo e sanitizado só para o emissor shadow."""
    record = ExecutionRecord(
        getattr(result, "provider", "unknown"),
        profile="shadow",
        execution_id=execution_id,
        deadlines=ExecutionDeadlines(),
    )
    # resultado já é terminal; transição direta ao estado final observado
    try:
        state = getattr(result, "state", None)
        target = ExecutionState(state.value) if hasattr(state, "value") else ExecutionState.FAILED
        for intermediate in (ExecutionState.STARTING, ExecutionState.RUNNING):
            if target in _ALLOWED_FROM.get(intermediate, frozenset()):
                record.transition(intermediate)
    except Exception as exc:  # noqa: BLE001 - reconstrução best-effort; falha não emite
        _shadow_rebuild_failures.append(f"{type(exc).__name__}")
    try:
        record.transition(ExecutionState(result.state.value))
    except Exception as exc:  # noqa: BLE001 - idem: falha de reconstrução só conta
        _shadow_rebuild_failures.append(f"{type(exc).__name__}")
    return record


_shadow_rebuild_failures: list[str] = []


_ALLOWED_FROM: dict[ExecutionState, frozenset[ExecutionState]] = {
    ExecutionState.STARTING: frozenset({ExecutionState.QUEUED}),
    ExecutionState.RUNNING: frozenset({ExecutionState.STARTING}),
}


class MCPServer:
    """Adaptar tools MCP aos contratos do nucleo, sem abrir rede ou processo."""

    def __init__(self, dependencies: MCPServerDependencies) -> None:
        self._dependencies = dependencies
        self._shadow = getattr(dependencies, "shadow_emitter", None)

    def run_combo(
        self,
        combo: ComboRequest,
        *,
        request_id: RequestId,
        verification: VerificationRequest | None = None,
        prepared: PreparedExecution | None = None,
    ) -> ToolPayload:
        return self._run(
            "run_combo",
            combo,
            request_id=request_id,
            verification=verification,
            prepared=prepared,
        )

    def ask_provider(
        self,
        combo: ComboRequest,
        *,
        request_id: RequestId,
        provider_id: object | None = None,
        task_type: object | None = None,
        working_directory: object | None = None,
        verification: VerificationRequest | None = None,
        prepared: PreparedExecution | None = None,
    ) -> ToolPayload:
        try:
            profile = self._dependencies.profile_resolver(
                explicit_profile_id=combo.profile,
                provider_id=provider_id,
                task_type=task_type,
                working_directory=working_directory,
            )
        except Exception:
            if prepared is not None:
                self._dependencies.registry.finalize(
                    prepared.execution_id, state=ExecutionState.FAILED.value
                )
            raise
        return self._run(
            "ask_provider",
            replace(combo, profile=profile),
            request_id=request_id,
            verification=verification,
            prepared=prepared,
        )

    def _run(
        self,
        tool: str,
        combo: ComboRequest,
        *,
        request_id: RequestId,
        verification: VerificationRequest | None,
        prepared: PreparedExecution | None,
    ) -> ToolPayload:
        if prepared is not None:
            if prepared.tool != tool or prepared.request_id != request_id:
                raise ValueError("prepared execution does not match the tool call")
            if combo.execution_id not in (None, prepared.execution_id):
                raise ValueError("prepared execution_id does not match the combo")
        execution_id = (
            prepared.execution_id
            if prepared is not None
            else combo.execution_id or uuid.uuid4().hex
        )
        routed_combo = replace(combo, execution_id=execution_id)
        if prepared is None:
            control = self._dependencies.control_factory()
            self._dependencies.registry.create(
                execution_id=execution_id,
                request_id=request_id,
                tool=tool,
                control=control,
            )
        else:
            control = prepared.control
        try:
            result = self._dependencies.router.run(routed_combo, control=control)
        except AllAttemptsFailed as exc:
            if exc.last_result is not None:
                self._dependencies.registry.update_attempt(
                    execution_id, _attempt_snapshot(exc.last_result)
                )
                state = exc.last_result.state.value
            else:
                state = ExecutionState.FAILED.value
            self._dependencies.registry.finalize(execution_id, state=state)
            raise
        except Exception:
            self._dependencies.registry.finalize(
                execution_id, state=ExecutionState.FAILED.value
            )
            raise

        self._dependencies.registry.update_attempt(
            execution_id, _attempt_snapshot(result)
        )
        if self._shadow is not None and result is not None:
            self._shadow.emit_transition(
                _record_for_shadow(execution_id, result), result.state,
                cancelled_by_client=False,
            )
        verification_result = None
        if verification is not None:
            try:
                verification_result = self._dependencies.verifier(
                    verification, control=control
                )
            except Exception:
                self._dependencies.registry.finalize(
                    execution_id, state=ExecutionState.FAILED.value
                )
                raise
        self._dependencies.registry.finalize(execution_id, state=result.state.value)
        payload: dict[str, Any] = {
            "execution_id": execution_id,
            "result": _result_payload(result),
        }
        if verification_result is not None:
            payload["verification"] = _verification_payload(verification_result)
        return payload

    def prepare_execution(
        self,
        tool: str,
        *,
        request_id: RequestId,
        execution_id: str | None = None,
    ) -> PreparedExecution:
        if tool not in {"run_combo", "ask_provider"}:
            raise ValueError(f"tool is not long-running: {tool}")
        if execution_id is not None and (
            not isinstance(execution_id, str) or not execution_id.strip()
        ):
            raise ValueError("execution_id must be a non-empty string")
        resolved_id = execution_id or uuid.uuid4().hex
        control = self._dependencies.control_factory()
        self._dependencies.registry.create(
            execution_id=resolved_id,
            request_id=request_id,
            tool=tool,
            control=control,
        )
        return PreparedExecution(resolved_id, request_id, tool, control)

    def abandon_nonterminal(self, *, reason: str = "client_abandoned") -> int:
        return self._dependencies.registry.abandon_all_nonterminal(reason=reason)

    def get_execution(
        self,
        execution_id: str | None = None,
        *,
        request_id: RequestId | None = None,
    ) -> ToolPayload:
        return {
            "execution": self._dependencies.registry.get(
                execution_id, request_id=request_id
            )
        }

    def list_executions(self, *, limit: int | None = None) -> ToolPayload:
        return {"executions": self._dependencies.registry.list(limit=limit)}

    def cancel_execution(
        self,
        execution_id: str | None = None,
        *,
        request_id: RequestId | None = None,
        reason: str | None = None,
    ) -> ToolPayload:
        result = self._dependencies.registry.request_cancel(
            execution_id=execution_id,
            request_id=request_id,
            reason=reason,
        )
        if result["found"]:
            result["requested"] = True
        return result


def run_combo(
    server: MCPServerContract,
    combo: ComboRequest,
    *,
    request_id: RequestId,
    verification: VerificationRequest | None = None,
    prepared: PreparedExecution | None = None,
) -> ToolPayload:
    """Handler direto da tool ``run_combo``."""
    return server.run_combo(
        combo,
        request_id=request_id,
        verification=verification,
        prepared=prepared,
    )


def ask_provider(
    server: MCPServerContract,
    combo: ComboRequest,
    *,
    request_id: RequestId,
    provider_id: object | None = None,
    task_type: object | None = None,
    working_directory: object | None = None,
    verification: VerificationRequest | None = None,
    prepared: PreparedExecution | None = None,
) -> ToolPayload:
    """Handler direto da tool ``ask_provider``."""
    return server.ask_provider(
        combo,
        request_id=request_id,
        provider_id=provider_id,
        task_type=task_type,
        working_directory=working_directory,
        verification=verification,
        prepared=prepared,
    )


def get_execution(
    server: MCPServerContract,
    execution_id: str | None = None,
    *,
    request_id: RequestId | None = None,
) -> ToolPayload:
    """Handler direto da tool ``get_execution``."""
    return server.get_execution(execution_id, request_id=request_id)


def list_executions(
    server: MCPServerContract, *, limit: int | None = None
) -> ToolPayload:
    """Handler direto da tool ``list_executions``."""
    return server.list_executions(limit=limit)


def cancel_execution(
    server: MCPServerContract,
    execution_id: str | None = None,
    *,
    request_id: RequestId | None = None,
    reason: str | None = None,
) -> ToolPayload:
    """Handler direto da tool ``cancel_execution``."""
    return server.cancel_execution(
        execution_id, request_id=request_id, reason=reason
    )
