"""Testes das primitivas modulares de execução."""

from __future__ import annotations

import ast
import json
import threading
from pathlib import Path

import pytest

from athena.execution import (
    TERMINAL_STATES,
    CancellationToken,
    Clock,
    DeadlineKind,
    ExecutionControl,
    ExecutionDeadlines,
    ExecutionRecord,
    ExecutionState,
    InvalidStateTransition,
)


class FakeClock:
    """Relógio monotônico controlado pelo teste."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def monotonic(self) -> float:
        """Retornar o instante controlado."""
        return self.now

    def advance(self, seconds: float) -> None:
        """Avançar o relógio controlado."""
        self.now += seconds


def _running_record(
    clock: FakeClock | None = None,
    deadlines: ExecutionDeadlines | None = None,
) -> ExecutionRecord:
    record = ExecutionRecord("test", clock=clock, deadlines=deadlines)
    record.transition(ExecutionState.STARTING)
    record.transition(ExecutionState.RUNNING)
    return record


def test_execution_state_has_exact_public_members() -> None:
    assert list(ExecutionState) == [
        ExecutionState.QUEUED,
        ExecutionState.STARTING,
        ExecutionState.RUNNING,
        ExecutionState.COMPLETED,
        ExecutionState.FAILED,
        ExecutionState.CANCELLED,
        ExecutionState.TIMED_OUT,
        ExecutionState.TERMINATION_UNCONFIRMED,
    ]


@pytest.mark.parametrize("terminal_state", sorted(TERMINAL_STATES, key=lambda state: state.value))
def test_each_terminal_state_rejects_a_later_transition(
    terminal_state: ExecutionState,
) -> None:
    record = _running_record()
    record.transition(terminal_state)

    with pytest.raises(InvalidStateTransition, match="is not allowed"):
        record.transition(ExecutionState.FAILED)

    assert record.state is terminal_state


def test_invalid_nonterminal_transition_is_explicitly_rejected() -> None:
    record = ExecutionRecord("test")

    with pytest.raises(InvalidStateTransition, match="queued to completed"):
        record.transition(ExecutionState.COMPLETED)


def test_absolute_and_idle_deadlines_are_independent() -> None:
    clock = FakeClock(100.0)
    record = _running_record(
        clock,
        ExecutionDeadlines(absolute_timeout_s=10.0, idle_timeout_s=4.0),
    )

    assert record.absolute_deadline_at == 110.0
    assert record.idle_deadline_at == 104.0

    clock.advance(3.0)
    assert record.record_progress()
    assert record.absolute_deadline_at == 110.0
    assert record.idle_deadline_at == 107.0

    clock.advance(4.0)
    assert record.expired_deadline() is DeadlineKind.IDLE


def test_absolute_deadline_survives_idle_heartbeats() -> None:
    clock = FakeClock(20.0)
    record = _running_record(
        clock,
        ExecutionDeadlines(absolute_timeout_s=5.0, idle_timeout_s=3.0),
    )

    clock.advance(2.0)
    record.record_progress()
    clock.advance(2.0)
    record.record_progress()
    clock.advance(1.0)

    assert record.deadline_status() == "absolute_deadline"
    assert record.idle_deadline_at == 27.0


def test_cancel_is_thread_safe_and_first_request_wins() -> None:
    control = CancellationToken()
    thread_count = 24
    barrier = threading.Barrier(thread_count)
    results: list[tuple[str, bool]] = []
    results_lock = threading.Lock()

    def cancel(reason: str) -> None:
        barrier.wait()
        won = control.request_cancel(reason)
        with results_lock:
            results.append((reason, won))

    threads = [
        threading.Thread(target=cancel, args=(f"reason-{index}",))
        for index in range(thread_count)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    winners = [reason for reason, won in results if won]
    assert len(winners) == 1
    assert control.cancellation_requested
    assert control.cancel_reason == winners[0]


def test_public_implementations_satisfy_protocols() -> None:
    assert isinstance(FakeClock(), Clock)
    assert isinstance(CancellationToken(), ExecutionControl)


def test_to_dict_omits_prompt_output_and_credentials() -> None:
    secret = "credential-secret-value"
    record = _running_record()
    record.prompt = f"prompt-{secret}"
    record.response = f"response-{secret}"
    record.output = f"output-{secret}"
    record.credential = secret
    record.transition(ExecutionState.CANCELLED, reason=secret)

    payload = record.to_dict()
    serialized = json.dumps(payload)

    assert secret not in serialized
    assert not {"prompt", "response", "output", "credential"} & payload.keys()
    assert payload["reason"] == "cancelled"


@pytest.mark.parametrize("field", ["absolute_timeout_s", "idle_timeout_s"])
def test_deadline_durations_must_be_positive(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        ExecutionDeadlines(**{field: 0.0})


def test_execution_package_has_no_import_from_another_core_package() -> None:
    package = Path(__file__).resolve().parents[1] / "athena" / "execution"

    for module in package.glob("*.py"):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level == 0
        }
        imported.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        assert not {
            name for name in imported if name == "athena" or name.startswith("athena.")
        }, module
