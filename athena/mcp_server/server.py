"""Implementação fina e sem transporte das tools MCP."""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Any

from athena.execution import ExecutionDeadlines, ExecutionRecord, ExecutionState
from athena.registry import RequestId
from athena.router import AllAttemptsFailed, ComboRequest, RoutingContext
from athena.tasks import (
    TaskHandleNotFound,
    TaskNotExecutable,
    TaskStoreUnavailable,
    TaskSubmission,
)
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
    "submit_task",
    "get_task",
)


def _result_payload(result: Any) -> dict[str, Any]:
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
    record = ExecutionRecord(
        getattr(result, "provider", "unknown"),
        profile="shadow",
        execution_id=execution_id,
        deadlines=ExecutionDeadlines(),
    )
    try:
        record.transition(ExecutionState(result.state.value))
    except Exception:  # noqa: BLE001, S110
        pass
    return record


_shadow_rebuild_failures: list[str] = []


class MCPServer:
    """Adaptar tools MCP aos contratos do núcleo, sem abrir rede ou processo."""

    def __init__(self, dependencies: MCPServerDependencies) -> None:
        self._dependencies = dependencies
        self._shadow = getattr(dependencies, "shadow_emitter", None)
        self._clio = getattr(dependencies, "clio_emitter", None)
        self._artifact_finalizer = getattr(dependencies, "artifact_finalizer", None)
        self._artifact_sink = getattr(dependencies, "artifact_sink", None)
        self._artifact_delivery_failures = 0

    def run_combo(
        self,
        combo: ComboRequest,
        *,
        request_id: RequestId,
        verification: VerificationRequest | None = None,
        prepared: PreparedExecution | None = None,
        routing_context: RoutingContext | None = None,
        task_handle: str | None = None,
    ) -> ToolPayload:
        return self._run(
            "run_combo",
            combo,
            request_id=request_id,
            verification=verification,
            prepared=prepared,
            routing_context=routing_context,
            task_handle=task_handle,
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
        routing_context: RoutingContext | None = None,
        task_handle: str | None = None,
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
            routing_context=routing_context,
            direct_provider_id=(
                provider_id if isinstance(provider_id, str) else None
            ),
            task_handle=task_handle,
        )

    def _flow_failure_close_and_finalize(
        self,
        *,
        execution_id: str,
        task_handle: str | None,
        flow_controller: Any,
        finalize_state: str,
        last_result: Any | None = None,
    ) -> None:
        """Finalize in-memory registry; durable-close failure has priority."""
        close_failed_exc: Exception | None = None
        if task_handle is not None and flow_controller is not None:
            try:
                flow_controller.close_failed(
                    task_handle, execution_id, ("FLOW_RUNNER_FAILURE",)
                )
            except Exception as cf_exc:  # noqa: BLE001
                close_failed_exc = cf_exc
        if last_result is not None:
            self._dependencies.registry.update_attempt(
                execution_id, _attempt_snapshot(last_result)
            )
        self._dependencies.registry.finalize(execution_id, state=finalize_state)
        if close_failed_exc is not None:
            if isinstance(close_failed_exc, TaskStoreUnavailable):
                raise close_failed_exc
            raise TaskStoreUnavailable("FLOW_CLOSE_FAILED") from None

    def _run(
        self,
        tool: str,
        combo: ComboRequest,
        *,
        request_id: RequestId,
        verification: VerificationRequest | None,
        prepared: PreparedExecution | None,
        routing_context: RoutingContext | None,
        direct_provider_id: str | None = None,
        task_handle: str | None = None,
    ) -> ToolPayload:
        flow_controller = self._dependencies.flow_controller

        # Correction 3: if task_handle present but no flow_controller, fail closed
        if task_handle is not None and flow_controller is None:
            raise TaskStoreUnavailable("FLOW_CONTROLLER_UNAVAILABLE")

        # Correction 1: verification + non-empty claims required when task_handle present
        if (
            task_handle is not None
            and flow_controller is not None
            and (
                verification is None
                or (not verification.files and not verification.commands)
            )
        ):
            raise ValueError("FLOW_VERIFICATION_MISSING")

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

        # FLOW-1: atomic queued→running before any runner
        # Correction 4: let TaskHandleNotFound/TaskNotExecutable propagate as-is
        if task_handle is not None and flow_controller is not None:
            try:
                flow_controller.begin(task_handle, execution_id)
            except (TaskHandleNotFound, TaskNotExecutable, TaskStoreUnavailable):
                self._dependencies.registry.finalize(
                    execution_id, state=ExecutionState.FAILED.value
                )
                raise  # propagate unchanged — MCPApplication maps to stable codes
            clio = self._clio
            if clio is not None:
                try:
                    clio.emit_flow_started(
                        task_handle=task_handle,
                        execution_id=execution_id,
                        tool=tool,
                    )
                except Exception:  # noqa: BLE001, S110
                    pass

        try:
            authority = self._dependencies.routing_authority
            if authority is not None:
                routed_combo = authority.plan(
                    routed_combo,
                    routing_context,
                    direct_provider_id=direct_provider_id,
                )
            result = self._dependencies.router.run(routed_combo, control=control)
        except AllAttemptsFailed as exc:
            finalize_state = (
                exc.last_result.state.value
                if exc.last_result is not None
                else ExecutionState.FAILED.value
            )
            self._flow_failure_close_and_finalize(
                execution_id=execution_id,
                task_handle=task_handle,
                flow_controller=flow_controller,
                finalize_state=finalize_state,
                last_result=exc.last_result,
            )
            raise
        except Exception:
            self._flow_failure_close_and_finalize(
                execution_id=execution_id,
                task_handle=task_handle,
                flow_controller=flow_controller,
                finalize_state=ExecutionState.FAILED.value,
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
                self._flow_failure_close_and_finalize(
                    execution_id=execution_id,
                    task_handle=task_handle,
                    flow_controller=flow_controller,
                    finalize_state=ExecutionState.FAILED.value,
                )
                raise

        self._dependencies.registry.finalize(execution_id, state=result.state.value)

        if self._artifact_finalizer is not None and self._artifact_sink is not None:
            try:
                envelope = {
                    "schema_version": "0.1",
                    "task_id": f"{tool}:{execution_id[:8]}",
                    "attempt_id": execution_id,
                    "declared_status": result.state.value,
                    "claims": [], "checks": [], "artifacts": [],
                    "telemetry": {
                        "exit_code": result.exit_code,
                        "duration_s": getattr(result, "duration_s", None),
                        "executor_id": tool,
                    },
                }
                report = self._artifact_finalizer(envelope)
                self._artifact_sink(report, execution_id=execution_id, tool=tool)
            except Exception:  # noqa: BLE001
                self._artifact_delivery_failures += 1

        payload: dict[str, Any] = {
            "execution_id": execution_id,
            "result": _result_payload(result),
        }
        if verification_result is not None:
            payload["verification"] = _verification_payload(verification_result)

        # FLOW-1: persist terminal projection and augment payload
        if task_handle is not None and flow_controller is not None:
            try:
                flow_payload = flow_controller.finish(
                    task_handle,
                    execution_id,
                    result,
                    verification_result,
                )
            except Exception as exc:
                close_failed_exc: Exception | None = None
                try:
                    flow_controller.close_failed(
                        task_handle, execution_id, ("FLOW_STORE_ERROR",)
                    )
                except Exception as cf_exc:  # noqa: BLE001
                    close_failed_exc = cf_exc
                if isinstance(exc, TaskStoreUnavailable):
                    raise
                if isinstance(close_failed_exc, TaskStoreUnavailable):
                    raise close_failed_exc
                raise
            for key in (
                "validation_status",
                "delivery_status",
                "chronos_action",
                "attempts_used",
                "reason_codes",
            ):
                if key in flow_payload:
                    payload[key] = flow_payload[key]
            payload["task_handle"] = task_handle
            clio = self._clio
            if clio is not None:
                try:
                    clio.emit_flow_finished(
                        task_handle=task_handle,
                        execution_id=execution_id,
                        tool=tool,
                        flow_payload=flow_payload,
                        execution_result=result,
                    )
                except Exception:  # noqa: BLE001, S110
                    pass

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

    def submit_task(self, submission: TaskSubmission) -> ToolPayload:
        if self._dependencies.task_store is None:
            raise TaskStoreUnavailable("task store not configured")
        result = self._dependencies.task_store.submit_task(submission)
        return {
            "task_handle": result.task_handle,
            "state": result.state,
            "created": result.created,
            "revision": result.revision,
            "created_at": result.created_at,
            "updated_at": result.updated_at,
        }

    def get_task(self, task_handle: str) -> ToolPayload:
        if self._dependencies.task_store is None:
            raise TaskStoreUnavailable("task store not configured")
        record = self._dependencies.task_store.get_task(task_handle)
        if record is None:
            return {"found": False}
        payload: dict[str, Any] = {
            "found": True,
            "task_handle": record.task_handle,
            "task_type": record.task_type,
            "state": record.state,
            "priority": record.priority,
            "revision": record.revision,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }
        # FLOW-1 projection fields — included only when present
        if record.execution_id is not None:
            payload["execution_id"] = record.execution_id
        if record.execution_status is not None:
            payload["execution_status"] = record.execution_status
        if record.validation_status is not None:
            payload["validation_status"] = record.validation_status
        if record.delivery_status is not None:
            payload["delivery_status"] = record.delivery_status
        if record.chronos_action is not None:
            payload["chronos_action"] = record.chronos_action
        if record.attempts_used is not None:
            payload["attempts_used"] = record.attempts_used
        if record.reason_codes is not None:
            payload["reason_codes"] = list(record.reason_codes)
        return payload

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


# Module-level handler shims

def run_combo(
    server: MCPServerContract,
    combo: ComboRequest,
    *,
    request_id: RequestId,
    verification: VerificationRequest | None = None,
    prepared: PreparedExecution | None = None,
    routing_context: RoutingContext | None = None,
    task_handle: str | None = None,
) -> ToolPayload:
    return server.run_combo(
        combo,
        request_id=request_id,
        verification=verification,
        prepared=prepared,
        routing_context=routing_context,
        task_handle=task_handle,
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
    routing_context: RoutingContext | None = None,
    task_handle: str | None = None,
) -> ToolPayload:
    return server.ask_provider(
        combo,
        request_id=request_id,
        provider_id=provider_id,
        task_type=task_type,
        working_directory=working_directory,
        verification=verification,
        prepared=prepared,
        routing_context=routing_context,
        task_handle=task_handle,
    )


def get_execution(
    server: MCPServerContract,
    execution_id: str | None = None,
    *,
    request_id: RequestId | None = None,
) -> ToolPayload:
    return server.get_execution(execution_id, request_id=request_id)


def list_executions(
    server: MCPServerContract, *, limit: int | None = None
) -> ToolPayload:
    return server.list_executions(limit=limit)


def cancel_execution(
    server: MCPServerContract,
    execution_id: str | None = None,
    *,
    request_id: RequestId | None = None,
    reason: str | None = None,
) -> ToolPayload:
    return server.cancel_execution(
        execution_id, request_id=request_id, reason=reason
    )


def submit_task(server: MCPServerContract, submission: TaskSubmission) -> ToolPayload:
    return server.submit_task(submission)


def get_task(server: MCPServerContract, task_handle: str) -> ToolPayload:
    return server.get_task(task_handle)
