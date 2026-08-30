"""Orquestração de combos com fallback e lease fail-closed."""

from __future__ import annotations

import uuid
from pathlib import Path

from aegis.classification import classify_service_profile
from aegis.contracts import FailureCondition, RiskContext
from aegis.decision import evaluate

from athena.bridge import BridgeRunnerContract, RunRequest, RunResult
from athena.capsule import CapsuleDenied
from athena.execution import (
    TERMINAL_STATES,
    Clock,
    ExecutionControl,
    ExecutionDeadlines,
    ExecutionRecord,
    ExecutionState,
    SystemClock,
)
from athena.lease import (
    DirectoryLeaseContract,
    LeaseAcquisitionTimeout,
    LeaseOwnershipError,
)

from .contracts import (
    AllAttemptsFailed,
    AttemptAuthorizerContract,
    ComboAttempt,
    ComboDeadlineExceeded,
    ComboRequest,
    FallbackBlocked,
)


class _HeldAttemptLease:
    """Expor ao bridge uma posse que o router já adquiriu."""

    def __init__(
        self,
        lease: DirectoryLeaseContract,
        workspace: Path,
        execution_id: str,
        attempt_id: str,
    ) -> None:
        self._lease = lease
        self._workspace = workspace
        self._execution_id = execution_id
        self._attempt_id = attempt_id

    def canonicalize(self, directory: str | Path) -> Path:
        return self._lease.canonicalize(directory)

    def _verify(
        self,
        directory: str | Path,
        execution_id: str,
        attempt_id: str,
    ) -> None:
        if (
            self.canonicalize(directory) != self._workspace
            or execution_id != self._execution_id
            or attempt_id != self._attempt_id
        ):
            raise LeaseOwnershipError("bridge requested a lease outside its held attempt")

    def acquire(
        self,
        directory: str | Path,
        execution_id: str,
        attempt_id: str,
        *,
        timeout: float | None = None,
    ) -> Path:
        self._verify(directory, execution_id, attempt_id)
        return self._workspace

    def transfer(
        self,
        directory: str | Path,
        execution_id: str,
        current_attempt_id: str,
        next_attempt_id: str,
    ) -> Path:
        self._verify(directory, execution_id, current_attempt_id)
        raise LeaseOwnershipError("only the router may transfer an attempt lease")

    def release(
        self,
        directory: str | Path,
        execution_id: str,
        attempt_id: str,
    ) -> None:
        self._verify(directory, execution_id, attempt_id)


def _confirmed_terminal(result: RunResult) -> bool:
    return (
        result.state in TERMINAL_STATES
        and result.state is not ExecutionState.TERMINATION_UNCONFIRMED
    )


def _failure_condition(attempt: ComboAttempt, result: RunResult) -> FailureCondition:
    if result.state is ExecutionState.TIMED_OUT:
        return FailureCondition.TIMEOUT
    if result.state is ExecutionState.CANCELLED:
        return FailureCondition.CANCELLATION
    return attempt.failure_condition


def _effective_deadlines(
    configured: ExecutionDeadlines,
    remaining: float | None,
) -> ExecutionDeadlines:
    if remaining is None:
        return configured
    absolute = configured.absolute_timeout_s
    idle = configured.idle_timeout_s
    return ExecutionDeadlines(
        absolute_timeout_s=remaining if absolute is None else min(absolute, remaining),
        idle_timeout_s=None if idle is None else min(idle, remaining),
    )


def _lease_timeout(request: RunRequest, remaining: float | None) -> float | None:
    configured = request.lease_timeout_s
    if remaining is None:
        return configured
    return remaining if configured is None else min(configured, remaining)


class ComboRouter:
    """Executar tentativas ordenadas sem concorrência acidental no workspace."""

    def __init__(
        self,
        bridge: BridgeRunnerContract,
        lease: DirectoryLeaseContract,
        *,
        clock: Clock | None = None,
        attempt_authorizer: AttemptAuthorizerContract | None = None,
    ) -> None:
        self._bridge = bridge
        self._lease = lease
        self._clock = clock or SystemClock()
        self._attempt_authorizer = attempt_authorizer

    def run(
        self,
        combo: ComboRequest,
        *,
        control: ExecutionControl | None = None,
    ) -> RunResult:
        """Executar até o primeiro sucesso ou uma condição terminal segura."""
        execution_id = combo.execution_id or str(uuid.uuid4())
        profile = classify_service_profile(combo.profile)
        started = self._clock.monotonic()
        deadline = (
            None
            if combo.overall_timeout_s is None
            else started + combo.overall_timeout_s
        )
        workspace = self._common_workspace(combo)
        current_attempt_id: str | None = None
        lease_owned = False
        release_confirmed = False
        last_result: RunResult | None = None

        try:
            for index, attempt in enumerate(combo.attempts):
                remaining = self._remaining(deadline)
                if remaining is not None and remaining <= 0:
                    raise ComboDeadlineExceeded(
                        "combo deadline exceeded before the next attempt",
                        last_result=last_result,
                    )

                next_attempt_id = str(uuid.uuid4())
                if current_attempt_id is None:
                    try:
                        workspace = self._lease.acquire(
                            workspace,
                            execution_id,
                            next_attempt_id,
                            timeout=_lease_timeout(attempt.request, remaining),
                        )
                    except LeaseAcquisitionTimeout as exc:
                        after_wait = self._remaining(deadline)
                        if after_wait is not None and after_wait <= 0:
                            raise ComboDeadlineExceeded(
                                "combo deadline exceeded while acquiring the workspace",
                                last_result=last_result,
                            ) from exc
                        raise
                    lease_owned = True
                else:
                    self._lease.transfer(
                        workspace,
                        execution_id,
                        current_attempt_id,
                        next_attempt_id,
                    )
                current_attempt_id = next_attempt_id
                release_confirmed = False

                execution = ExecutionRecord(
                    attempt.provider,
                    profile=profile.value,
                    execution_id=execution_id,
                    attempt_id=current_attempt_id,
                    deadlines=_effective_deadlines(attempt.deadlines, remaining),
                    clock=self._clock,
                )
                held_lease = _HeldAttemptLease(
                    self._lease,
                    workspace,
                    execution_id,
                    current_attempt_id,
                )
                try:
                    request = attempt.request
                    if self._attempt_authorizer is not None:
                        prepare = self._attempt_authorizer.prepare_attempt
                        request = prepare(
                            request,
                            execution,
                            fallback_declared=index + 1 < len(combo.attempts),
                            tests=combo.tests,
                        )
                    last_result = self._bridge.run(
                        request,
                        execution,
                        held_lease,
                        control=control,
                    )
                except CapsuleDenied as exc:
                    release_confirmed = True
                    raise AllAttemptsFailed(
                        exc.reason_code,
                        last_result=last_result,
                    ) from exc
                release_confirmed = _confirmed_terminal(last_result)

                if last_result.state is ExecutionState.COMPLETED:
                    return last_result

                has_fallback = index + 1 < len(combo.attempts)
                if not has_fallback:
                    raise AllAttemptsFailed(
                        "all combo attempts failed",
                        last_result=last_result,
                    )

                remaining = self._remaining(deadline)
                if remaining is not None and remaining <= 0:
                    raise ComboDeadlineExceeded(
                        "combo deadline exceeded before fallback",
                        last_result=last_result,
                    )

                condition = _failure_condition(attempt, last_result)
                decision = evaluate(
                    RiskContext(
                        requested_action="automatic_fallback",
                        explicit_profile_id=profile,
                        failure_condition=condition,
                    )
                )
                if not decision.approved:
                    raise AllAttemptsFailed(
                        f"automatic fallback is disabled for profile {profile.value}",
                        last_result=last_result,
                    )

                if not release_confirmed:
                    raise FallbackBlocked(
                        "fallback blocked because previous termination was not confirmed",
                        last_result=last_result,
                    )

            raise AssertionError("validated combo unexpectedly had no attempts")
        finally:
            if lease_owned and release_confirmed and current_attempt_id is not None:
                self._lease.release(workspace, execution_id, current_attempt_id)

    def _common_workspace(self, combo: ComboRequest) -> Path:
        workspaces = tuple(
            self._lease.canonicalize(attempt.request.cwd) for attempt in combo.attempts
        )
        if any(workspace != workspaces[0] for workspace in workspaces[1:]):
            raise ValueError("all combo attempts must use the same workspace")
        return workspaces[0]

    def _remaining(self, deadline: float | None) -> float | None:
        if deadline is None:
            return None
        return max(0.0, deadline - self._clock.monotonic())


def run_combo(
    combo: ComboRequest,
    *,
    bridge: BridgeRunnerContract,
    lease: DirectoryLeaseContract,
    control: ExecutionControl | None = None,
    clock: Clock | None = None,
) -> RunResult:
    """Atalho funcional para executar um combo com dependências explícitas."""
    return ComboRouter(bridge, lease, clock=clock).run(combo, control=control)
