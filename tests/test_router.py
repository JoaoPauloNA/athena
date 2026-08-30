"""Testes do router de combos com dependências integralmente simuladas."""

from __future__ import annotations

import ast
from collections.abc import Sequence
from pathlib import Path

import pytest

from athena.bridge import BridgeRunnerContract, RunRequest, RunResult
from athena.execution import Clock, ExecutionRecord, ExecutionState
from athena.lease import DirectoryLeaseManager, LeaseAcquisitionTimeout
from athena.profiles import FailureCondition, ServiceProfile
from athena.router import (
    AllAttemptsFailed,
    ComboAttempt,
    ComboDeadlineExceeded,
    ComboRequest,
    ComboRouter,
    ComboRouterContract,
    FallbackBlocked,
    run_combo,
)


class FakeClock:
    """Relógio monotônico controlado pelo bridge falso."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class RecordingLease(DirectoryLeaseManager):
    """Lease real em memória com trilha de operações do router."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[tuple[str, str, str]] = []

    def acquire(
        self,
        directory: str | Path,
        execution_id: str,
        attempt_id: str,
        *,
        timeout: float | None = None,
    ) -> Path:
        result = super().acquire(
            directory,
            execution_id,
            attempt_id,
            timeout=timeout,
        )
        self.events.append(("acquire", execution_id, attempt_id))
        return result

    def transfer(
        self,
        directory: str | Path,
        execution_id: str,
        current_attempt_id: str,
        next_attempt_id: str,
    ) -> Path:
        result = super().transfer(
            directory,
            execution_id,
            current_attempt_id,
            next_attempt_id,
        )
        self.events.append(("transfer", current_attempt_id, next_attempt_id))
        return result

    def release(
        self,
        directory: str | Path,
        execution_id: str,
        attempt_id: str,
    ) -> None:
        self.events.append(("release", execution_id, attempt_id))
        super().release(directory, execution_id, attempt_id)


class FakeBridge:
    """Bridge sem processo que respeita o mesmo contrato de lease e lifecycle."""

    def __init__(
        self,
        states: Sequence[ExecutionState],
        *,
        clock: FakeClock | None = None,
        elapsed_per_run: float = 0.0,
    ) -> None:
        self._states = iter(states)
        self._clock = clock
        self._elapsed_per_run = elapsed_per_run
        self.providers: list[str] = []
        self.deadlines: list[float | None] = []

    def run(
        self,
        request: RunRequest,
        execution: ExecutionRecord,
        lease: object,
        *,
        control: object = None,
    ) -> RunResult:
        lease_contract = lease
        workspace = lease_contract.acquire(  # type: ignore[attr-defined]
            request.cwd,
            execution.execution_id,
            execution.attempt_id,
            timeout=request.lease_timeout_s,
        )
        self.providers.append(execution.provider)
        self.deadlines.append(execution.absolute_deadline_s)
        state = next(self._states)
        if state is ExecutionState.TIMED_OUT:
            execution.transition(state)
        else:
            execution.transition(ExecutionState.STARTING)
            execution.transition(ExecutionState.RUNNING)
            execution.transition(state)
        if self._clock is not None:
            self._clock.advance(self._elapsed_per_run)
        lease_contract.release(  # type: ignore[attr-defined]
            workspace,
            execution.execution_id,
            execution.attempt_id,
        )
        return RunResult(
            command=tuple(request.command),
            cwd=Path(workspace),
            state=state,
            exit_code=0 if state is ExecutionState.COMPLETED else 1,
            stdout="ok" if state is ExecutionState.COMPLETED else "",
            stderr="",
            duration_s=self._elapsed_per_run,
            error=None if state is ExecutionState.COMPLETED else state.value,
        )


def _combo(
    tmp_path: Path,
    *,
    profile: ServiceProfile = ServiceProfile.CODE_AGENT,
    condition: FailureCondition = FailureCondition.PROVIDER_ERROR,
    overall_timeout_s: float | None = None,
) -> ComboRequest:
    return ComboRequest(
        attempts=(
            ComboAttempt("primary", RunRequest(("primary",), tmp_path), failure_condition=condition),
            ComboAttempt("fallback", RunRequest(("fallback",), tmp_path)),
        ),
        profile=profile,
        overall_timeout_s=overall_timeout_s,
        execution_id="combo-execution",
    )


def test_combo_falls_back_in_order_through_bridge_contract(tmp_path: Path) -> None:
    bridge = FakeBridge((ExecutionState.FAILED, ExecutionState.COMPLETED))
    lease = RecordingLease()

    result = run_combo(_combo(tmp_path), bridge=bridge, lease=lease)

    assert result.state is ExecutionState.COMPLETED
    assert bridge.providers == ["primary", "fallback"]
    assert [event[0] for event in lease.events] == ["acquire", "transfer", "release"]
    acquire, transfer, release = lease.events
    assert acquire[2] == transfer[1]
    assert transfer[2] == release[2]


def test_termination_unconfirmed_raises_fallback_blocked_and_keeps_lease(
    tmp_path: Path,
) -> None:
    bridge = FakeBridge((ExecutionState.TERMINATION_UNCONFIRMED,))
    lease = RecordingLease()
    combo = _combo(tmp_path)

    with pytest.raises(FallbackBlocked) as raised:
        ComboRouter(bridge, lease).run(combo)

    assert raised.value.last_result is not None
    assert raised.value.last_result.state is ExecutionState.TERMINATION_UNCONFIRMED
    assert bridge.providers == ["primary"]
    assert [event[0] for event in lease.events] == ["acquire"]
    with pytest.raises(LeaseAcquisitionTimeout):
        lease.acquire(tmp_path, "competing-execution", "attempt", timeout=0)

    _, execution_id, attempt_id = lease.events[0]
    lease.release(tmp_path, execution_id, attempt_id)


def test_combo_deadline_expiry_before_fallback_has_priority(tmp_path: Path) -> None:
    clock = FakeClock()
    bridge = FakeBridge(
        (ExecutionState.TERMINATION_UNCONFIRMED,),
        clock=clock,
        elapsed_per_run=1.0,
    )
    lease = RecordingLease()

    with pytest.raises(ComboDeadlineExceeded) as raised:
        ComboRouter(bridge, lease, clock=clock).run(
            _combo(tmp_path, overall_timeout_s=1.0)
        )

    assert raised.value.last_result is not None
    assert raised.value.last_result.state is ExecutionState.TERMINATION_UNCONFIRMED
    assert bridge.providers == ["primary"]
    assert bridge.deadlines == [1.0]
    assert [event[0] for event in lease.events] == ["acquire"]

    _, execution_id, attempt_id = lease.events[0]
    lease.release(tmp_path, execution_id, attempt_id)


@pytest.mark.parametrize(
    "profile",
    [ServiceProfile.AUTHENTICATED_EXTERNAL, ServiceProfile.UNKNOWN],
)
@pytest.mark.parametrize(
    ("state", "condition"),
    [
        (ExecutionState.TIMED_OUT, FailureCondition.TIMEOUT),
        (ExecutionState.FAILED, FailureCondition.NETWORK_ERROR),
        (ExecutionState.FAILED, FailureCondition.PROVIDER_ERROR),
        (ExecutionState.FAILED, FailureCondition.OTHER),
        (ExecutionState.CANCELLED, FailureCondition.CANCELLATION),
    ],
)
def test_sensitive_and_unknown_profiles_never_fallback(
    tmp_path: Path,
    profile: ServiceProfile,
    state: ExecutionState,
    condition: FailureCondition,
) -> None:
    bridge = FakeBridge((state,))
    lease = RecordingLease()

    with pytest.raises(AllAttemptsFailed, match="fallback is disabled"):
        ComboRouter(bridge, lease).run(
            _combo(tmp_path, profile=profile, condition=condition)
        )

    assert bridge.providers == ["primary"]
    assert [event[0] for event in lease.events] == ["acquire", "release"]


def test_all_failed_attempts_release_the_last_transferred_lease(tmp_path: Path) -> None:
    bridge = FakeBridge((ExecutionState.FAILED, ExecutionState.FAILED))
    lease = RecordingLease()

    with pytest.raises(AllAttemptsFailed) as raised:
        ComboRouter(bridge, lease).run(_combo(tmp_path))

    assert raised.value.last_result is not None
    assert bridge.providers == ["primary", "fallback"]
    assert [event[0] for event in lease.events] == ["acquire", "transfer", "release"]


def test_combo_requires_one_canonical_workspace(tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    combo = ComboRequest(
        attempts=(
            ComboAttempt("first", RunRequest(("first",), tmp_path)),
            ComboAttempt("second", RunRequest(("second",), other)),
        ),
        profile=ServiceProfile.CODE_AGENT,
    )
    bridge = FakeBridge((ExecutionState.COMPLETED,))

    with pytest.raises(ValueError, match="same workspace"):
        ComboRouter(bridge, RecordingLease()).run(combo)

    assert bridge.providers == []


def test_public_implementations_satisfy_router_dependencies(tmp_path: Path) -> None:
    bridge = FakeBridge((ExecutionState.COMPLETED,))
    router = ComboRouter(bridge, RecordingLease())

    assert isinstance(bridge, BridgeRunnerContract)
    assert isinstance(router, ComboRouterContract)
    assert isinstance(FakeClock(), Clock)


def test_router_imports_only_its_four_allowed_athena_packages() -> None:
    package = Path(__file__).resolve().parents[1] / "athena" / "router"
    imported_core_packages: set[str] = set()

    for module in package.glob("*.py"):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported_core_packages.add(node.module)
            elif isinstance(node, ast.Import):
                imported_core_packages.update(alias.name for alias in node.names)

    assert {
        name.split(".")[1]
        for name in imported_core_packages
        if name.startswith("athena.")
    } <= {"bridge", "capsule", "execution", "lease", "profiles"}
