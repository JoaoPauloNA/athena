"""Suite P0 ponta a ponta pela superficie do mcp_server."""

from __future__ import annotations

from pathlib import Path

import pytest

from athena.bridge import RunRequest, RunResult
from athena.execution import (
    TERMINAL_STATES,
    CancellationToken,
    DeadlineKind,
    ExecutionDeadlines,
    ExecutionRecord,
    ExecutionState,
    InvalidStateTransition,
)
from athena.lease import DirectoryLeaseManager
from athena.mcp_server import MCPServer, MCPServerDependencies
from athena.profiles import ServiceProfile, resolve_service_profile
from athena.registry import ExecutionRegistry
from athena.router import (
    AllAttemptsFailed,
    ComboAttempt,
    ComboRequest,
    ComboRouter,
    FallbackBlocked,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class P0Bridge:
    def __init__(
        self,
        clock: FakeClock,
        states: tuple[ExecutionState, ...],
        *,
        timeout_kind: DeadlineKind | None = None,
    ) -> None:
        self.clock = clock
        self.states = list(states)
        self.timeout_kind = timeout_kind
        self.providers: list[str] = []
        self.terminal_transition_blocked = False

    def run(
        self,
        request: RunRequest,
        execution: ExecutionRecord,
        lease: object,
        *,
        control: object = None,
    ) -> RunResult:
        self.providers.append(execution.provider)
        execution.transition(ExecutionState.STARTING)
        execution.transition(ExecutionState.RUNNING)
        state = self.states.pop(0)
        expired = None
        if self.timeout_kind is DeadlineKind.IDLE:
            self.clock.advance(execution.idle_deadline_s or 0.0)
            expired = execution.expired_deadline()
            state = ExecutionState.TIMED_OUT
        elif self.timeout_kind is DeadlineKind.ABSOLUTE:
            idle_step = (execution.idle_deadline_s or 1.0) / 2
            while execution.expired_deadline() is None:
                self.clock.advance(idle_step)
                execution.record_progress()
            expired = execution.expired_deadline()
            state = ExecutionState.TIMED_OUT
        execution.transition(state)
        try:
            execution.transition(ExecutionState.FAILED)
        except InvalidStateTransition:
            self.terminal_transition_blocked = True
        return RunResult(
            tuple(request.command),
            Path(request.cwd),
            state,
            0 if state is ExecutionState.COMPLETED else None,
            "",
            "",
            self.clock.now,
            expired_deadline=expired,
        )


def _server(bridge: P0Bridge, clock: FakeClock) -> MCPServer:
    return MCPServer(
        MCPServerDependencies(
            router=ComboRouter(bridge, DirectoryLeaseManager(), clock=clock),
            registry=ExecutionRegistry(),
            verifier=lambda request, *, control: None,  # type: ignore[arg-type]
            profile_resolver=resolve_service_profile,
            control_factory=CancellationToken,
        )
    )


def _combo(
    tmp_path: Path,
    *,
    deadlines: ExecutionDeadlines | None = None,
    fallback: bool = False,
    execution_id: str,
) -> ComboRequest:
    attempts = [
        ComboAttempt(
            "primary",
            RunRequest(("simulated",), tmp_path),
            deadlines=deadlines or ExecutionDeadlines(),
        )
    ]
    if fallback:
        attempts.append(
            ComboAttempt("fallback", RunRequest(("simulated",), tmp_path))
        )
    return ComboRequest(
        attempts=attempts,
        profile=ServiceProfile.CODE_AGENT,
        execution_id=execution_id,
    )


@pytest.mark.parametrize(
    ("kind", "deadlines"),
    [
        (DeadlineKind.IDLE, ExecutionDeadlines(idle_timeout_s=2.0)),
        (
            DeadlineKind.ABSOLUTE,
            ExecutionDeadlines(absolute_timeout_s=3.0, idle_timeout_s=2.0),
        ),
    ],
)
def test_p0_idle_and_absolute_timeout_end_to_end(
    tmp_path: Path,
    kind: DeadlineKind,
    deadlines: ExecutionDeadlines,
) -> None:
    clock = FakeClock()
    bridge = P0Bridge(clock, (ExecutionState.RUNNING,), timeout_kind=kind)
    server = _server(bridge, clock)
    execution_id = f"timeout-{kind.value}"

    with pytest.raises(AllAttemptsFailed):
        server.run_combo(
            _combo(tmp_path, deadlines=deadlines, execution_id=execution_id),
            request_id=execution_id,
        )

    execution = server.get_execution(execution_id)["execution"]
    assert execution["state"] == "timed_out"
    assert execution["attempts"][0]["reason"] == kind.value
    assert bridge.terminal_transition_blocked


@pytest.mark.parametrize("terminal_state", tuple(TERMINAL_STATES))
def test_p0_terminal_lifecycle_never_transitions_through_surface(
    tmp_path: Path, terminal_state: ExecutionState
) -> None:
    clock = FakeClock()
    bridge = P0Bridge(clock, (terminal_state,))
    server = _server(bridge, clock)
    combo = _combo(tmp_path, execution_id=f"lifecycle-{terminal_state.value}")

    if terminal_state is ExecutionState.COMPLETED:
        server.run_combo(combo, request_id=terminal_state.value)
    else:
        with pytest.raises(AllAttemptsFailed):
            server.run_combo(combo, request_id=terminal_state.value)

    assert bridge.terminal_transition_blocked
    execution = server.get_execution(combo.execution_id)["execution"]
    assert execution["state"] == terminal_state.value


def test_p0_fallback_is_blocked_without_confirmed_termination(tmp_path: Path) -> None:
    clock = FakeClock()
    bridge = P0Bridge(clock, (ExecutionState.TERMINATION_UNCONFIRMED,))
    server = _server(bridge, clock)
    combo = _combo(tmp_path, fallback=True, execution_id="fallback-blocked")

    with pytest.raises(FallbackBlocked):
        server.run_combo(combo, request_id="request-fallback")

    assert bridge.providers == ["primary"]
    execution = server.get_execution("fallback-blocked")["execution"]
    assert execution["state"] == "termination_unconfirmed"
