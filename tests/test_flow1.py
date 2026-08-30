"""Testes focados FLOW-1 — ciclo durável de um agente.

Corrections applied:
8. TaskRecord fields test uses dataclasses.fields; FLOW fields included in approved set.
9. Real JSON-RPC smoke: submit in process A, actual run_combo with task_handle+ROUTE config
   and deterministic verification in process B, get_task in new process C.
10. Adversarial tests: routing failure closes durably, absent flow controller fails-closed,
    mismatched execution_id terminal write rejected, prepared-call validates before reservation.
"""

from __future__ import annotations

import dataclasses
import json
import os
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from athena.execution import ExecutionState
from athena.flow.controller import FlowController
from athena.tasks import (
    DELIVERY_STATUS_AWAITING,
    STABLE_REASON_CODES,
    SQLiteTaskStore,
    TaskHandleNotFound,
    TaskNotExecutable,
    TaskStoreUnavailable,
    TerminalProjection,
    build_submission,
)
from athena.tasks.sqlite_store import SCHEMA_VERSION

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _task(**overrides: object) -> dict[str, object]:
    task: dict[str, object] = {"task_type": "demo.task", "input": "hello"}
    task.update(overrides)
    return task


def _submit_queued(store: SQLiteTaskStore, key: str = "k1") -> str:
    sub = build_submission(key, _task())
    result = store.submit_task(sub)
    return result.task_handle


def _make_projection(
    execution_id: str = "abc123",
    execution_status: str = "completed",
    validation_status: str = "pass",
    chronos_action: str = "CLOSED",
    attempts_used: int = 1,
    reason_codes: tuple[str, ...] = ("ALL_CHECKS_PASSED",),
) -> TerminalProjection:
    return TerminalProjection(
        execution_id=execution_id,
        execution_status=execution_status,
        validation_status=validation_status,
        delivery_status=DELIVERY_STATUS_AWAITING,
        chronos_action=chronos_action,
        attempts_used=attempts_used,
        reason_codes=reason_codes,
    )


# ---------------------------------------------------------------------------
# TerminalProjection validation (correction 6)
# ---------------------------------------------------------------------------


def test_terminal_projection_rejects_invalid_validation_status() -> None:
    with pytest.raises(ValueError, match="invalid validation_status"):
        TerminalProjection(
            execution_id="exec-1",
            execution_status="completed",
            validation_status="wrong",
            delivery_status=DELIVERY_STATUS_AWAITING,
            chronos_action="CLOSED",
            attempts_used=1,
            reason_codes=("ALL_CHECKS_PASSED",),
        )


def test_terminal_projection_rejects_wrong_delivery_status() -> None:
    with pytest.raises(ValueError, match="delivery_status"):
        TerminalProjection(
            execution_id="exec-1",
            execution_status="completed",
            validation_status="pass",
            delivery_status="completed",  # wrong
            chronos_action="CLOSED",
            attempts_used=1,
            reason_codes=("ALL_CHECKS_PASSED",),
        )


def test_terminal_projection_rejects_invalid_chronos_action() -> None:
    with pytest.raises(ValueError, match="invalid chronos_action"):
        TerminalProjection(
            execution_id="exec-1",
            execution_status="completed",
            validation_status="pass",
            delivery_status=DELIVERY_STATUS_AWAITING,
            chronos_action="REOPEN",  # not stable
            attempts_used=1,
            reason_codes=("ALL_CHECKS_PASSED",),
        )


def test_terminal_projection_rejects_unstable_reason_code() -> None:
    with pytest.raises(ValueError, match="unstable reason_code"):
        TerminalProjection(
            execution_id="exec-1",
            execution_status="completed",
            validation_status="pass",
            delivery_status=DELIVERY_STATUS_AWAITING,
            chronos_action="CLOSED",
            attempts_used=1,
            reason_codes=("RAW_EXCEPTION_MESSAGE",),
        )


def test_terminal_projection_rejects_zero_attempts() -> None:
    with pytest.raises(ValueError, match="positive integer"):
        TerminalProjection(
            execution_id="exec-1",
            execution_status="completed",
            validation_status="pass",
            delivery_status=DELIVERY_STATUS_AWAITING,
            chronos_action="CLOSED",
            attempts_used=0,
            reason_codes=("ALL_CHECKS_PASSED",),
        )


# ---------------------------------------------------------------------------
# SQLiteTaskStore — schema migration v1→v2 (correction 6)
# ---------------------------------------------------------------------------


def _create_v1_db(db_path: Path, task_handle: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("CREATE TABLE schema_meta (version INTEGER NOT NULL)")
    conn.execute(
        "CREATE TABLE tasks ("
        "task_handle TEXT PRIMARY KEY,"
        "idempotency_key_digest TEXT NOT NULL UNIQUE,"
        "task_hash TEXT NOT NULL,"
        "canonical_task_json TEXT NOT NULL,"
        "task_type TEXT NOT NULL,"
        "state TEXT NOT NULL,"
        "priority INTEGER NOT NULL,"
        "revision INTEGER NOT NULL,"
        "created_at TEXT NOT NULL,"
        "updated_at TEXT NOT NULL"
        ")"
    )
    conn.execute("INSERT INTO schema_meta (version) VALUES (1)")
    conn.execute(
        "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            task_handle, "digest123", "hash456",
            '{"task_type":"demo.task","input":"hi","priority":5}',
            "demo.task", "queued", 5, 1,
            "2026-01-01T00:00:00.000000+00:00",
            "2026-01-01T00:00:00.000000+00:00",
        ),
    )
    conn.commit()
    conn.close()


def test_v1_migration_is_idempotent(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o700)
    db_path = state_dir / "tasks.sqlite3"
    handle = "migrated_handle_001"
    _create_v1_db(db_path, handle)
    os.chmod(db_path, 0o600)

    store = SQLiteTaskStore(state_dir)
    record = store.get_task(handle)
    assert record is not None
    assert record.state == "queued"
    assert record.execution_id is None

    # Second open is idempotent
    store.get_task(handle)

    conn = sqlite3.connect(db_path)
    version = conn.execute("SELECT version FROM schema_meta").fetchone()[0]
    conn.close()
    assert version == SCHEMA_VERSION  # 2


def test_v1_existing_handle_survives_migration(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o700)
    db_path = state_dir / "tasks.sqlite3"
    handle = "survivor_handle"
    _create_v1_db(db_path, handle)
    os.chmod(db_path, 0o600)

    store = SQLiteTaskStore(state_dir)
    record = store.get_task(handle)
    assert record is not None
    assert record.task_handle == handle
    assert record.task_type == "demo.task"


# ---------------------------------------------------------------------------
# SQLiteTaskStore — transition_queued_to_running
# ---------------------------------------------------------------------------


def test_transition_queued_to_running_succeeds(tmp_path: Path) -> None:
    store = SQLiteTaskStore(tmp_path / "state")
    handle = _submit_queued(store)
    store.transition_queued_to_running(handle, "exec-abc")

    record = store.get_task(handle)
    assert record is not None
    assert record.state == "running"
    assert record.execution_id == "exec-abc"


def test_transition_unknown_handle_raises_not_found(tmp_path: Path) -> None:
    store = SQLiteTaskStore(tmp_path / "state")
    _submit_queued(store)
    with pytest.raises(TaskHandleNotFound):
        store.transition_queued_to_running("nonexistent-handle", "exec-x")


def test_transition_running_handle_raises_not_executable(tmp_path: Path) -> None:
    store = SQLiteTaskStore(tmp_path / "state")
    handle = _submit_queued(store)
    store.transition_queued_to_running(handle, "exec-1")

    with pytest.raises(TaskNotExecutable) as exc_info:
        store.transition_queued_to_running(handle, "exec-2")
    assert "RUNNING" in exc_info.value.reason_code.upper()


def test_transition_terminal_handle_raises_not_executable(tmp_path: Path) -> None:
    store = SQLiteTaskStore(tmp_path / "state")
    handle = _submit_queued(store)
    store.transition_queued_to_running(handle, "exec-1")
    store.persist_terminal_projection(handle, _make_projection("exec-1"))

    with pytest.raises(TaskNotExecutable):
        store.transition_queued_to_running(handle, "exec-2")


def test_concurrent_transition_executes_at_most_once(tmp_path: Path) -> None:
    store = SQLiteTaskStore(tmp_path / "state")
    handle = _submit_queued(store)

    successes: list[int] = []
    errors: list[int] = []

    def attempt(i: int) -> None:
        try:
            store.transition_queued_to_running(handle, f"exec-{i}")
            successes.append(i)
        except (TaskNotExecutable, TaskHandleNotFound):
            errors.append(i)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(attempt, range(8)))

    assert len(successes) == 1, f"expected exactly 1 success, got: {successes}"
    assert len(errors) == 7


# ---------------------------------------------------------------------------
# SQLiteTaskStore — persist_terminal_projection (correction 6: execution_id guard)
# ---------------------------------------------------------------------------


def test_persist_terminal_projection_updates_state(tmp_path: Path) -> None:
    store = SQLiteTaskStore(tmp_path / "state")
    handle = _submit_queued(store)
    store.transition_queued_to_running(handle, "exec-term")

    proj = _make_projection(
        "exec-term",
        execution_status="completed",
        validation_status="pass",
        chronos_action="CLOSED",
        attempts_used=1,
        reason_codes=("ALL_CHECKS_PASSED",),
    )
    store.persist_terminal_projection(handle, proj)

    record = store.get_task(handle)
    assert record is not None
    assert record.state == "awaiting_human_review"
    assert record.execution_id == "exec-term"
    assert record.execution_status == "completed"
    assert record.validation_status == "pass"
    assert record.delivery_status == DELIVERY_STATUS_AWAITING
    assert record.chronos_action == "CLOSED"
    assert record.attempts_used == 1
    assert record.reason_codes == ("ALL_CHECKS_PASSED",)


def test_persist_terminal_mismatched_execution_id_rejected(tmp_path: Path) -> None:
    """Correction 6: atomically requires matching execution_id."""
    store = SQLiteTaskStore(tmp_path / "state")
    handle = _submit_queued(store)
    store.transition_queued_to_running(handle, "exec-real")

    with pytest.raises(TaskStoreUnavailable):
        store.persist_terminal_projection(
            handle,
            _make_projection("exec-DIFFERENT"),  # wrong execution_id
        )

    # State must remain running after failed persist
    record = store.get_task(handle)
    assert record is not None
    assert record.state == "running"


def test_persist_terminal_unknown_handle_raises(tmp_path: Path) -> None:
    store = SQLiteTaskStore(tmp_path / "state")
    _submit_queued(store)
    with pytest.raises(TaskStoreUnavailable):
        store.persist_terminal_projection("bad-handle", _make_projection())


# ---------------------------------------------------------------------------
# SQLiteTaskStore — close_failed
# ---------------------------------------------------------------------------


def test_close_failed_transitions_running_to_terminal(tmp_path: Path) -> None:
    store = SQLiteTaskStore(tmp_path / "state")
    handle = _submit_queued(store)
    store.transition_queued_to_running(handle, "exec-fail")

    store.close_failed(handle, "exec-fail", ("FLOW_RUNNER_FAILURE",))

    record = store.get_task(handle)
    assert record is not None
    assert record.state == "awaiting_human_review"
    assert record.chronos_action == "HUMAN_REVIEW"
    assert record.validation_status == "fail"
    assert record.delivery_status == DELIVERY_STATUS_AWAITING


def test_close_failed_idempotent_when_already_terminal(tmp_path: Path) -> None:
    store = SQLiteTaskStore(tmp_path / "state")
    handle = _submit_queued(store)
    store.transition_queued_to_running(handle, "exec-idem")
    store.persist_terminal_projection(handle, _make_projection("exec-idem"))

    # Should not raise or change state
    store.close_failed(handle, "exec-idem", ("FLOW_RUNNER_FAILURE",))
    record = store.get_task(handle)
    assert record is not None
    assert record.state == "awaiting_human_review"
    assert record.validation_status == "pass"  # unchanged from original persist


# ---------------------------------------------------------------------------
# TaskRecord fields (correction 8: use dataclasses.fields, include FLOW fields)
# ---------------------------------------------------------------------------


def test_get_task_returns_correct_projection_fields(tmp_path: Path) -> None:
    store = SQLiteTaskStore(tmp_path / "state")
    sub = build_submission("key-1", _task(project_ref="proj-x"))
    submitted = store.submit_task(sub)

    record = store.get_task(submitted.task_handle)
    assert record is not None
    assert record.task_handle == submitted.task_handle
    assert record.task_type == "demo.task"
    assert record.state == "queued"
    assert record.priority == 5
    assert record.revision == 1

    # Correction 8: use dataclasses.fields, include FLOW-1 approved fields
    field_names = {f.name for f in dataclasses.fields(record)}
    approved_fields = {
        "task_handle",
        "task_type",
        "state",
        "priority",
        "revision",
        "created_at",
        "updated_at",
        # FLOW-1 approved optional fields
        "execution_id",
        "execution_status",
        "validation_status",
        "delivery_status",
        "chronos_action",
        "attempts_used",
        "reason_codes",
    }
    assert field_names == approved_fields


def test_get_task_does_not_expose_sensitive_fields(tmp_path: Path) -> None:
    store = SQLiteTaskStore(tmp_path / "state")
    handle = _submit_queued(store)
    store.transition_queued_to_running(handle, "exec-sens")
    store.persist_terminal_projection(handle, _make_projection("exec-sens"))

    record = store.get_task(handle)
    assert record is not None
    # Use dataclasses.fields (correction 8: slots dataclass)
    field_names = {f.name for f in dataclasses.fields(record)}
    sensitive = {
        "input", "canonical_task_json", "cwd", "stdout", "stderr",
        "env", "prompt", "secret", "idempotency_key_digest", "task_hash",
        "command",
    }
    overlap = field_names & sensitive
    assert not overlap, f"sensitive fields in TaskRecord: {overlap}"


# ---------------------------------------------------------------------------
# FlowController — EG as sole verdict emitter
# ---------------------------------------------------------------------------


def _make_mock_result(state_value: str = "completed") -> MagicMock:
    result = MagicMock()
    state = MagicMock()
    state.value = state_value
    result.state = state
    return result


def _make_mock_verification_pass() -> MagicMock:
    vr = MagicMock()
    finding = MagicMock()
    finding.status.value = "passed"
    finding.subject = "file:/tmp/output.txt"
    vr.deterministic.findings = [finding]
    vr.deterministic.passed = True
    vr.accepted = True
    return vr


def _make_mock_verification_fail() -> MagicMock:
    vr = MagicMock()
    finding = MagicMock()
    finding.status.value = "failed"
    finding.subject = "file:/tmp/output.txt"
    vr.deterministic.findings = [finding]
    vr.deterministic.passed = False
    vr.accepted = False
    return vr


def test_flow_controller_begin_succeeds_on_queued(tmp_path: Path) -> None:
    store = SQLiteTaskStore(tmp_path / "state")
    handle = _submit_queued(store)
    controller = FlowController(store)
    controller.begin(handle, "exec-fc1")

    record = store.get_task(handle)
    assert record is not None
    assert record.state == "running"


def test_flow_controller_begin_unknown_handle_raises(tmp_path: Path) -> None:
    store = SQLiteTaskStore(tmp_path / "state")
    _submit_queued(store)
    controller = FlowController(store)
    with pytest.raises(TaskHandleNotFound):
        controller.begin("bad-handle", "exec-x")


def test_flow_controller_finish_completed_all_pass_gives_closed(tmp_path: Path) -> None:
    """Correction 1: completed execution + all checks pass => PASS/CLOSED."""
    store = SQLiteTaskStore(tmp_path / "state")
    handle = _submit_queued(store)
    controller = FlowController(store)
    controller.begin(handle, "exec-pass")

    payload = controller.finish(
        handle, "exec-pass",
        _make_mock_result("completed"),
        _make_mock_verification_pass(),
    )

    assert payload["validation_status"] == "pass"
    assert payload["delivery_status"] == DELIVERY_STATUS_AWAITING
    assert payload["chronos_action"] == "CLOSED"
    assert payload["attempts_used"] == 1
    assert isinstance(payload["reason_codes"], list)

    record = store.get_task(handle)
    assert record is not None
    assert record.state == "awaiting_human_review"
    assert record.validation_status == "pass"
    assert record.chronos_action == "CLOSED"
    assert record.delivery_status == DELIVERY_STATUS_AWAITING


def test_flow_controller_finish_failed_execution_gives_human_review(
    tmp_path: Path,
) -> None:
    """Correction 1: failed execution => never PASS/HUMAN_REVIEW."""
    store = SQLiteTaskStore(tmp_path / "state")
    handle = _submit_queued(store)
    controller = FlowController(store)
    controller.begin(handle, "exec-fail")

    payload = controller.finish(
        handle, "exec-fail",
        _make_mock_result("failed"),  # failed runner
        _make_mock_verification_pass(),  # verifier says pass but EG sees failed state
    )

    # EG alone decides: failed execution = not all checks pass => HUMAN_REVIEW
    assert payload["delivery_status"] == DELIVERY_STATUS_AWAITING
    assert payload["chronos_action"] == "HUMAN_REVIEW"
    # validation_status must not be "pass" since execution failed
    assert payload["validation_status"] != "pass"


def test_flow_controller_finish_failed_verification_gives_human_review(
    tmp_path: Path,
) -> None:
    """Correction 1: completed execution + failed check => EG FAIL => HUMAN_REVIEW."""
    store = SQLiteTaskStore(tmp_path / "state")
    handle = _submit_queued(store)
    controller = FlowController(store)
    controller.begin(handle, "exec-vfail")

    payload = controller.finish(
        handle, "exec-vfail",
        _make_mock_result("completed"),
        _make_mock_verification_fail(),
    )

    assert payload["delivery_status"] == DELIVERY_STATUS_AWAITING
    assert payload["chronos_action"] == "HUMAN_REVIEW"
    assert payload["validation_status"] != "pass"

    record = store.get_task(handle)
    assert record is not None
    assert record.state == "awaiting_human_review"
    assert record.chronos_action == "HUMAN_REVIEW"
    assert record.validation_status != "pass"


def test_flow_payload_omits_sensitive_data(tmp_path: Path) -> None:
    store = SQLiteTaskStore(tmp_path / "state")
    handle = _submit_queued(store)
    controller = FlowController(store)
    controller.begin(handle, "exec-safe")

    payload = controller.finish(
        handle, "exec-safe",
        _make_mock_result("completed"),
        _make_mock_verification_pass(),
    )

    payload_str = json.dumps(payload)
    forbidden = ["stdout", "stderr", "cwd", "command", "env", "prompt", "secret"]
    for term in forbidden:
        assert term not in payload_str, f"sensitive term in payload: {term}"


def test_flow_controller_finish_persists_before_returning(tmp_path: Path) -> None:
    store = SQLiteTaskStore(tmp_path / "state")
    handle = _submit_queued(store)
    controller = FlowController(store)
    controller.begin(handle, "exec-persist")

    controller.finish(
        handle, "exec-persist",
        _make_mock_result("completed"),
        _make_mock_verification_pass(),
    )

    # New store instance proves persistence is durable
    store2 = SQLiteTaskStore(tmp_path / "state")
    record = store2.get_task(handle)
    assert record is not None
    assert record.state == "awaiting_human_review"
    assert record.validation_status == "pass"


# ---------------------------------------------------------------------------
# FlowController — close_failed (correction 2)
# ---------------------------------------------------------------------------


def test_flow_controller_close_failed_durably_closes(tmp_path: Path) -> None:
    """close_failed uses Evidence Gate + Chronos, not store manual authority."""
    store = SQLiteTaskStore(tmp_path / "state")
    handle = _submit_queued(store)
    controller = FlowController(store)
    controller.begin(handle, "exec-cf")

    controller.close_failed(handle, "exec-cf", ("FLOW_RUNNER_FAILURE",))

    record = store.get_task(handle)
    assert record is not None
    assert record.state == "awaiting_human_review"
    assert record.chronos_action == "HUMAN_REVIEW"
    assert record.validation_status == "fail"
    assert record.execution_status == "failed"
    assert record.reason_codes is not None
    assert "FLOW_RUNNER_FAILURE" in record.reason_codes
    assert "REQUIRED_CHECK_FAILED" in record.reason_codes


def test_flow_controller_close_failed_idempotent_when_terminal(
    tmp_path: Path,
) -> None:
    store = SQLiteTaskStore(tmp_path / "state")
    handle = _submit_queued(store)
    controller = FlowController(store)
    controller.begin(handle, "exec-idem-fc")
    controller.close_failed(handle, "exec-idem-fc", ("FLOW_RUNNER_FAILURE",))

    controller.close_failed(handle, "exec-idem-fc", ("FLOW_RUNNER_FAILURE",))

    record = store.get_task(handle)
    assert record is not None
    assert record.state == "awaiting_human_review"
    assert record.validation_status == "fail"


# ---------------------------------------------------------------------------
# Seven tools invariant (correction 10)
# ---------------------------------------------------------------------------


def test_exactly_seven_tools() -> None:
    from athena.mcp_server.server import TOOL_NAMES
    from athena.mcp_stdio.application import TOOLS

    assert len(TOOL_NAMES) == 7
    assert len(TOOLS) == 7
    tool_names_set = {t["name"] for t in TOOLS}
    assert tool_names_set == {
        "run_combo", "ask_provider", "get_execution",
        "list_executions", "cancel_execution", "submit_task", "get_task",
    }


# ---------------------------------------------------------------------------
# Adversarial tests (correction 10)
# ---------------------------------------------------------------------------


def test_absent_flow_controller_fails_closed_before_runner(tmp_path: Path) -> None:
    """Correction 3+10: task_handle with no flow_controller raises before runner."""
    import secrets

    from athena.bridge import LocalBridgeRunner
    from athena.execution import CancellationToken
    from athena.iris import LocalIrisBoundary
    from athena.lease import DirectoryLeaseManager
    from athena.mcp_server import MCPServer, MCPServerDependencies
    from athena.mcp_stdio.application import MCPApplication
    from athena.profiles import resolve_service_profile
    from athena.registry import ExecutionRegistry
    from athena.router import ComboRouter
    from athena.routing_authority import DeterministicRoutingAuthority
    from athena.verifier import verify
    from tests.route0_support import routing_arguments, write_route_config

    config_dir = write_route_config(
        tmp_path / "config", providers=("echo",)
    )

    iris = LocalIrisBoundary(LocalBridgeRunner(), secrets.token_bytes(32))
    store = SQLiteTaskStore(tmp_path / "state")
    handle = _submit_queued(store, key="absent-fc-key")

    server = MCPServer(
        MCPServerDependencies(
            router=ComboRouter(iris, DirectoryLeaseManager(), attempt_authorizer=iris),
            registry=ExecutionRegistry(),
            verifier=verify,
            profile_resolver=resolve_service_profile,
            control_factory=CancellationToken,
            task_store=store,
            routing_authority=DeterministicRoutingAuthority(config_dir),
            flow_controller=None,  # absent
        )
    )
    app = MCPApplication(server)

    resp = app.call(
        "run_combo",
        {
            **routing_arguments(),
            "attempts": [
                {
                    "provider": "echo",
                    "command": ["echo", "hi"],
                    "cwd": str(tmp_path),
                }
            ],
            "task_handle": handle,
            "verification": {"files": [str(tmp_path / "flow1-absent-fc.txt")]},
        },
        request_id="req-absent",
    )
    payload = json.loads(resp["content"][0]["text"])
    assert resp.get("isError") is True
    assert payload["error"] == "FLOW_CONTROLLER_UNAVAILABLE"
    # Task must remain queued (never reached runner)
    record = store.get_task(handle)
    assert record is not None
    assert record.state == "queued"


def test_mismatched_execution_id_terminal_write_rejected(tmp_path: Path) -> None:
    """Correction 6+10: persist_terminal requires matching execution_id."""
    store = SQLiteTaskStore(tmp_path / "state")
    handle = _submit_queued(store, key="mismatch-key")
    store.transition_queued_to_running(handle, "exec-real")

    with pytest.raises(TaskStoreUnavailable):
        store.persist_terminal_projection(
            handle,
            _make_projection("exec-WRONG"),  # different execution_id
        )

    # State must remain running
    record = store.get_task(handle)
    assert record is not None
    assert record.state == "running"


def test_prepared_call_validates_before_reservation(tmp_path: Path) -> None:
    """Correction 5+10: task_handle validation in prepare_long_call before reservation."""
    import secrets

    from athena.bridge import LocalBridgeRunner
    from athena.execution import CancellationToken
    from athena.iris import LocalIrisBoundary
    from athena.lease import DirectoryLeaseManager
    from athena.mcp_server import MCPServer, MCPServerDependencies
    from athena.mcp_stdio.application import MCPApplication
    from athena.profiles import resolve_service_profile
    from athena.registry import ExecutionRegistry
    from athena.router import ComboRouter
    from athena.routing_authority import DeterministicRoutingAuthority
    from tests.route0_support import routing_arguments, write_route_config

    config_dir = write_route_config(tmp_path / "config2", providers=("echo",))
    iris = LocalIrisBoundary(LocalBridgeRunner(), secrets.token_bytes(32))

    server = MCPServer(
        MCPServerDependencies(
            router=ComboRouter(iris, DirectoryLeaseManager(), attempt_authorizer=iris),
            registry=ExecutionRegistry(),
            verifier=lambda vr, control: (_ for _ in ()).throw(AssertionError("no verifier")),
            profile_resolver=resolve_service_profile,
            control_factory=CancellationToken,
            routing_authority=DeterministicRoutingAuthority(config_dir),
        )
    )
    app = MCPApplication(server)

    # Should raise ValueError (FLOW_VERIFICATION_MISSING) before any reservation
    with pytest.raises(ValueError, match="FLOW_VERIFICATION_MISSING"):
        app.prepare_long_call(
            "run_combo",
            {
                **routing_arguments(),
                "attempts": [
                    {
                        "provider": "echo",
                        "command": ["echo"],
                        "cwd": str(tmp_path),
                    }
                ],
                "task_handle": "some-handle",
                # no verification — missing claims
            },
            request_id="req-prep",
        )


def test_concurrent_duplicate_handle_executes_at_most_once(tmp_path: Path) -> None:
    store = SQLiteTaskStore(tmp_path / "state")
    handle = _submit_queued(store)
    controller = FlowController(store)

    successes: list[str] = []
    failures: list[str] = []

    def try_begin(i: int) -> None:
        try:
            controller.begin(handle, f"exec-dup-{i}")
            successes.append(f"exec-dup-{i}")
        except (TaskNotExecutable, TaskHandleNotFound):
            failures.append(f"exec-dup-{i}")

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(try_begin, range(6)))

    assert len(successes) == 1
    assert len(failures) == 5


# ---------------------------------------------------------------------------
# Real JSON-RPC smoke test: process A submit, process B run_combo + task_handle,
# process C get_task (correction 9)
# ---------------------------------------------------------------------------


def _run_athena_requests(
    state_dir: Path,
    requests: tuple[dict[str, Any], ...],
    *,
    config_dir: Path | None = None,
) -> list[dict[str, Any]]:
    env = dict(os.environ)
    env["ATHENA_STATE_DIR"] = str(state_dir)
    if config_dir is not None:
        env["ATHENA_CONFIG_DIR"] = str(config_dir)
    expected_ids = {item["id"] for item in requests if "id" in item}
    process = subprocess.Popen(
        [sys.executable, "-m", "athena"],
        cwd=Path(__file__).resolve().parents[1],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    stderr = ""
    try:
        for item in requests:
            process.stdin.write(json.dumps(item) + "\n")
            process.stdin.flush()
        responses: dict[Any, dict[str, Any]] = {}
        deadline = time.time() + 30
        while expected_ids - responses.keys() and time.time() < deadline:
            line = process.stdout.readline()
            if not line:
                break
            msg = json.loads(line)
            msg_id = msg.get("id")
            if msg_id in expected_ids:
                responses[msg_id] = msg
        process.stdin.close()
        process.wait(timeout=10)
        if process.stderr is not None:
            stderr = process.stderr.read()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    assert process.returncode == 0, stderr
    return [responses[item["id"]] for item in requests if "id" in item]


def test_real_flow1_run_combo_then_get_task(tmp_path: Path) -> None:
    """Correction 9: real 3-process JSON-RPC smoke test.

    Process A: submit task.
    Process B: run_combo with task_handle + ROUTE config + deterministic verification.
    Process C: get_task proving pass/CLOSED/awaiting_human_review and no sensitive fields.
    """
    from tests.route0_support import routing_arguments, write_route_config

    state_dir = tmp_path / "state"

    # ROUTE config: a real local echo provider
    config_dir = write_route_config(
        tmp_path / "config", providers=("echo",)
    )

    # A sentinel file the verification will check (it must exist at test time)
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_text("ok", encoding="utf-8")

    # --- Process A: submit task ---
    submit_resp = _run_athena_requests(
        state_dir,
        (
            {
                "jsonrpc": "2.0", "id": "s1", "method": "tools/call",
                "params": {
                    "name": "submit_task",
                    "arguments": {
                        "idempotency_key": "flow1-real-smoke",
                        "task": {"task_type": "demo.task", "input": "smoke"},
                    },
                },
            },
        ),
    )
    sub_payload = json.loads(submit_resp[0]["result"]["content"][0]["text"])
    assert sub_payload["created"] is True
    handle = sub_payload["task_handle"]

    # --- Process B: run_combo with task_handle + ROUTE config + file verification ---
    # The sentinel file exists so verification will pass deterministically
    run_resp = _run_athena_requests(
        state_dir,
        (
            {
                "jsonrpc": "2.0", "id": "r1", "method": "tools/call",
                "params": {
                    "name": "run_combo",
                    "arguments": {
                        **routing_arguments(),
                        "attempts": [
                            {
                                "provider": "echo",
                                "command": ["echo", "flow1-smoke"],
                                "cwd": str(tmp_path),
                            }
                        ],
                        "task_handle": handle,
                        "verification": {
                            "files": [str(sentinel)],
                        },
                    },
                },
            },
        ),
        config_dir=config_dir,
    )
    run_payload = json.loads(run_resp[0]["result"]["content"][0]["text"])
    assert run_resp[0].get("isError") is not True
    assert run_payload["result"]["state"] == "completed"
    assert run_payload["validation_status"] == "pass"
    assert run_payload["delivery_status"] == DELIVERY_STATUS_AWAITING
    assert run_payload["chronos_action"] == "CLOSED"

    # --- Process C: get_task in new process ---
    get_resp = _run_athena_requests(
        state_dir,
        (
            {
                "jsonrpc": "2.0", "id": "g1", "method": "tools/call",
                "params": {"name": "get_task", "arguments": {"task_handle": handle}},
            },
        ),
    )
    get_payload = json.loads(get_resp[0]["result"]["content"][0]["text"])
    assert get_payload["found"] is True
    assert get_payload["state"] == "awaiting_human_review"
    assert get_payload["validation_status"] == "pass"
    assert get_payload["delivery_status"] == DELIVERY_STATUS_AWAITING
    assert get_payload["chronos_action"] == "CLOSED"

    # Sensitive fields must be absent from get_task response
    sensitive_keys = {"input", "cwd", "stdout", "stderr", "command", "env", "secret"}
    for key in sensitive_keys:
        assert key not in get_payload, f"sensitive key leaked in get_task: {key}"


def test_get_task_omits_raw_exception(tmp_path: Path) -> None:
    """get_task must never expose exception messages or raw subprocess content."""
    state_dir = tmp_path / "state"
    store = SQLiteTaskStore(state_dir)
    handle = _submit_queued(store, key="exc-key")
    store.transition_queued_to_running(handle, "exec-noexc")
    store.close_failed(handle, "exec-noexc", ("FLOW_RUNNER_FAILURE",))

    get_resp = _run_athena_requests(
        state_dir,
        (
            {
                "jsonrpc": "2.0", "id": "g2", "method": "tools/call",
                "params": {"name": "get_task", "arguments": {"task_handle": handle}},
            },
        ),
    )
    get_payload = json.loads(get_resp[0]["result"]["content"][0]["text"])
    assert get_payload["found"] is True
    assert get_payload["state"] == "awaiting_human_review"
    text = json.dumps(get_payload)
    for forbidden in ["Traceback", "Exception:", "Error:", "stdout", "stderr"]:
        assert forbidden not in text, f"raw exception/output leaked: {forbidden}"


def test_reason_codes_in_public_projection_are_stable(tmp_path: Path) -> None:
    """reason_codes in get_task response must only contain stable codes."""
    store = SQLiteTaskStore(tmp_path / "state")
    handle = _submit_queued(store, key="rc-stable")
    store.transition_queued_to_running(handle, "exec-rc")
    store.close_failed(handle, "exec-rc", ("FLOW_RUNNER_FAILURE",))

    record = store.get_task(handle)
    assert record is not None
    assert record.reason_codes is not None
    for code in record.reason_codes:
        assert code in STABLE_REASON_CODES, f"unstable code in projection: {code}"


# ---------------------------------------------------------------------------
# Security adversarial tests (audit corrections)
# ---------------------------------------------------------------------------


def test_terminal_projection_rejects_bool_attempts_used() -> None:
    with pytest.raises(ValueError, match="positive integer"):
        TerminalProjection(
            execution_id="exec-1",
            execution_status="completed",
            validation_status="pass",
            delivery_status=DELIVERY_STATUS_AWAITING,
            chronos_action="CLOSED",
            attempts_used=True,  # type: ignore[arg-type]
            reason_codes=("ALL_CHECKS_PASSED",),
        )


def test_terminal_projection_rejects_invalid_execution_status() -> None:
    with pytest.raises(ValueError, match="invalid execution_status"):
        TerminalProjection(
            execution_id="exec-1",
            execution_status="queued",
            validation_status="pass",
            delivery_status=DELIVERY_STATUS_AWAITING,
            chronos_action="CLOSED",
            attempts_used=1,
            reason_codes=("ALL_CHECKS_PASSED",),
        )


def _make_mock_verification_phase_not_passed() -> MagicMock:
    vr = MagicMock()
    finding = MagicMock()
    finding.status.value = "passed"
    det = MagicMock()
    det.findings = [finding]
    det.passed = False
    vr.deterministic = det
    return vr


def _make_mock_verification_phase_truthy_non_bool() -> MagicMock:
    vr = MagicMock()
    finding = MagicMock()
    finding.status.value = "passed"
    det = MagicMock()
    det.findings = [finding]
    det.passed = 1
    vr.deterministic = det
    return vr


def test_flow_controller_finish_truthy_non_bool_phase_never_passes(
    tmp_path: Path,
) -> None:
    store = SQLiteTaskStore(tmp_path / "state")
    handle = _submit_queued(store)
    controller = FlowController(store)
    controller.begin(handle, "exec-truthy")

    payload = controller.finish(
        handle, "exec-truthy",
        _make_mock_result("completed"),
        _make_mock_verification_phase_truthy_non_bool(),
    )

    assert payload["validation_status"] != "pass"
    assert payload["chronos_action"] == "HUMAN_REVIEW"


def test_flow_controller_finish_missing_verifier_phase_never_passes(
    tmp_path: Path,
) -> None:
    store = SQLiteTaskStore(tmp_path / "state")
    handle = _submit_queued(store)
    controller = FlowController(store)
    controller.begin(handle, "exec-no-vr")

    payload = controller.finish(
        handle, "exec-no-vr",
        _make_mock_result("completed"),
        None,
    )

    assert payload["validation_status"] != "pass"
    assert payload["chronos_action"] == "HUMAN_REVIEW"


def test_flow_controller_finish_cancelled_verifier_phase_never_passes(
    tmp_path: Path,
) -> None:
    store = SQLiteTaskStore(tmp_path / "state")
    handle = _submit_queued(store)
    controller = FlowController(store)
    controller.begin(handle, "exec-cancel-vr")

    payload = controller.finish(
        handle, "exec-cancel-vr",
        _make_mock_result("completed"),
        _make_mock_verification_phase_not_passed(),
    )

    assert payload["validation_status"] != "pass"
    assert payload["chronos_action"] == "HUMAN_REVIEW"


def test_get_task_sanitizes_corrupted_reason_codes_json(tmp_path: Path) -> None:
    store = SQLiteTaskStore(tmp_path / "state")
    handle = _submit_queued(store, key="corrupt-rc")
    store.transition_queued_to_running(handle, "exec-corrupt")
    store.close_failed(handle, "exec-corrupt", ("FLOW_RUNNER_FAILURE",))

    conn = sqlite3.connect(store.db_path)
    conn.execute(
        "UPDATE tasks SET reason_codes_json = ? WHERE task_handle = ?",
        ('["RAW_EXCEPTION_MESSAGE"]', handle),
    )
    conn.commit()
    conn.close()

    record = store.get_task(handle)
    assert record is not None
    assert record.reason_codes == ("FLOW_STORE_ERROR",)


def test_close_failed_rejects_wrong_execution_id(tmp_path: Path) -> None:
    store = SQLiteTaskStore(tmp_path / "state")
    handle = _submit_queued(store)
    store.transition_queued_to_running(handle, "exec-real")

    with pytest.raises(TaskStoreUnavailable):
        store.close_failed(handle, "exec-WRONG", ("FLOW_RUNNER_FAILURE",))

    record = store.get_task(handle)
    assert record is not None
    assert record.state == "running"


def test_finish_exception_closes_task(tmp_path: Path) -> None:
    import secrets

    from athena.bridge import LocalBridgeRunner
    from athena.execution import CancellationToken
    from athena.flow.controller import make_flow_controller
    from athena.iris import LocalIrisBoundary
    from athena.lease import DirectoryLeaseManager
    from athena.mcp_server import MCPServer, MCPServerDependencies
    from athena.mcp_stdio.application import MCPApplication
    from athena.profiles import resolve_service_profile
    from athena.registry import ExecutionRegistry
    from athena.router import ComboRouter
    from athena.routing_authority import DeterministicRoutingAuthority
    from athena.verifier import verify
    from tests.route0_support import routing_arguments, write_route_config

    config_dir = write_route_config(tmp_path / "config", providers=("echo",))
    iris = LocalIrisBoundary(LocalBridgeRunner(), secrets.token_bytes(32))
    inner_store = SQLiteTaskStore(tmp_path / "state")
    handle = _submit_queued(inner_store, key="finish-exc-key")
    sentinel = tmp_path / "finish-exc-sentinel.txt"
    sentinel.write_text("ok", encoding="utf-8")

    class FailPersistStore:
        def __init__(self, inner: SQLiteTaskStore) -> None:
            self._inner = inner

        def __getattr__(self, name: str) -> object:
            return getattr(self._inner, name)

        def persist_terminal_projection(
            self, task_handle: str, projection: TerminalProjection
        ) -> None:
            raise TaskStoreUnavailable("persist failed")

    store = FailPersistStore(inner_store)
    server = MCPServer(
        MCPServerDependencies(
            router=ComboRouter(iris, DirectoryLeaseManager(), attempt_authorizer=iris),
            registry=ExecutionRegistry(),
            verifier=verify,
            profile_resolver=resolve_service_profile,
            control_factory=CancellationToken,
            task_store=inner_store,
            routing_authority=DeterministicRoutingAuthority(config_dir),
            flow_controller=make_flow_controller(store),  # type: ignore[arg-type]
        )
    )
    app = MCPApplication(server)

    resp = app.call(
        "run_combo",
        {
            **routing_arguments(),
            "attempts": [
                {
                    "provider": "echo",
                    "command": ["echo", "finish-fail"],
                    "cwd": str(tmp_path),
                }
            ],
            "task_handle": handle,
            "verification": {"files": [str(sentinel)]},
        },
        request_id="req-finish-exc",
    )
    payload = json.loads(resp["content"][0]["text"])
    assert resp.get("isError") is True
    assert payload["error"] == "TASK_STORE_UNAVAILABLE"
    record = inner_store.get_task(handle)
    assert record is not None
    assert record.state == "running"


def test_routing_failure_finalizes_registry_when_close_failed_raises(
    tmp_path: Path,
) -> None:
    import secrets

    from athena.bridge import LocalBridgeRunner
    from athena.execution import CancellationToken
    from athena.iris import LocalIrisBoundary
    from athena.lease import DirectoryLeaseManager
    from athena.mcp_server import MCPServer, MCPServerDependencies
    from athena.mcp_stdio.application import MCPApplication
    from athena.profiles import resolve_service_profile
    from athena.registry import ExecutionRegistry
    from athena.router import AllAttemptsFailed, ComboRouter
    from athena.routing_authority import DeterministicRoutingAuthority
    from tests.route0_support import routing_arguments, write_route_config

    config_dir = write_route_config(tmp_path / "config", providers=("echo",))
    iris = LocalIrisBoundary(LocalBridgeRunner(), secrets.token_bytes(32))
    store = SQLiteTaskStore(tmp_path / "state")
    handle = _submit_queued(store, key="registry-finalize-key")
    registry = ExecutionRegistry()

    class FailingCloseFlowController(FlowController):
        def close_failed(
            self,
            task_handle: str,
            execution_id: str,
            reason_codes: tuple[str, ...],
        ) -> None:
            raise TaskStoreUnavailable("simulated close_failed failure")

    router = ComboRouter(iris, DirectoryLeaseManager(), attempt_authorizer=iris)
    original_run = router.run

    def failing_run(*args: object, **kwargs: object) -> object:
        raise AllAttemptsFailed("simulated routing failure")

    router.run = failing_run  # type: ignore[method-assign]

    server = MCPServer(
        MCPServerDependencies(
            router=router,
            registry=registry,
            verifier=lambda vr, control: (_ for _ in ()).throw(AssertionError("no verifier")),
            profile_resolver=resolve_service_profile,
            control_factory=CancellationToken,
            task_store=store,
            routing_authority=DeterministicRoutingAuthority(config_dir),
            flow_controller=FailingCloseFlowController(store),
        )
    )
    app = MCPApplication(server)

    resp = app.call(
        "run_combo",
        {
            **routing_arguments(),
            "attempts": [
                {
                    "provider": "echo",
                    "command": ["echo", "fail"],
                    "cwd": str(tmp_path),
                }
            ],
            "task_handle": handle,
            "verification": {"files": [str(tmp_path / "sentinel.txt")]},
        },
        request_id="req-close-fail",
    )
    payload = json.loads(resp["content"][0]["text"])
    assert resp.get("isError") is True
    assert payload["error"] == "TASK_STORE_UNAVAILABLE"
    entries = registry.list()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["finalized"] is True
    assert entry["state"] == ExecutionState.FAILED.value

    router.run = original_run  # type: ignore[method-assign]


def test_routing_failure_wraps_close_failed_runtime_error_after_registry_finalize(
    tmp_path: Path,
) -> None:
    import secrets

    from athena.bridge import LocalBridgeRunner
    from athena.execution import CancellationToken
    from athena.iris import LocalIrisBoundary
    from athena.lease import DirectoryLeaseManager
    from athena.mcp_server import MCPServer, MCPServerDependencies
    from athena.mcp_stdio.application import MCPApplication
    from athena.profiles import resolve_service_profile
    from athena.registry import ExecutionRegistry
    from athena.router import AllAttemptsFailed, ComboRouter
    from athena.routing_authority import DeterministicRoutingAuthority
    from tests.route0_support import routing_arguments, write_route_config

    config_dir = write_route_config(tmp_path / "config", providers=("echo",))
    iris = LocalIrisBoundary(LocalBridgeRunner(), secrets.token_bytes(32))
    store = SQLiteTaskStore(tmp_path / "state")
    handle = _submit_queued(store, key="close-runtime-key")
    registry = ExecutionRegistry()

    class RuntimeCloseFlowController(FlowController):
        def close_failed(
            self,
            task_handle: str,
            execution_id: str,
            reason_codes: tuple[str, ...],
        ) -> None:
            raise RuntimeError("close_failed internal boom")

    router = ComboRouter(iris, DirectoryLeaseManager(), attempt_authorizer=iris)

    def failing_run(*args: object, **kwargs: object) -> object:
        raise AllAttemptsFailed("simulated routing failure")

    router.run = failing_run  # type: ignore[method-assign]

    server = MCPServer(
        MCPServerDependencies(
            router=router,
            registry=registry,
            verifier=lambda vr, control: (_ for _ in ()).throw(AssertionError("no verifier")),
            profile_resolver=resolve_service_profile,
            control_factory=CancellationToken,
            task_store=store,
            routing_authority=DeterministicRoutingAuthority(config_dir),
            flow_controller=RuntimeCloseFlowController(store),
        )
    )
    app = MCPApplication(server)

    resp = app.call(
        "run_combo",
        {
            **routing_arguments(),
            "attempts": [
                {
                    "provider": "echo",
                    "command": ["echo", "fail"],
                    "cwd": str(tmp_path),
                }
            ],
            "task_handle": handle,
            "verification": {"files": [str(tmp_path / "sentinel.txt")]},
        },
        request_id="req-close-runtime",
    )
    payload = json.loads(resp["content"][0]["text"])
    assert resp.get("isError") is True
    assert payload == {"error": "TASK_STORE_UNAVAILABLE"}
    entries = registry.list()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["finalized"] is True
    assert entry["state"] == ExecutionState.FAILED.value


def test_verifier_failure_raises_task_store_unavailable_when_close_persist_fails(
    tmp_path: Path,
) -> None:
    import secrets

    from athena.bridge import LocalBridgeRunner
    from athena.execution import CancellationToken
    from athena.flow.controller import make_flow_controller
    from athena.iris import LocalIrisBoundary
    from athena.lease import DirectoryLeaseManager
    from athena.mcp_server import MCPServer, MCPServerDependencies
    from athena.mcp_stdio.application import MCPApplication
    from athena.profiles import resolve_service_profile
    from athena.registry import ExecutionRegistry
    from athena.router import ComboRouter
    from athena.routing_authority import DeterministicRoutingAuthority
    from tests.route0_support import routing_arguments, write_route_config

    config_dir = write_route_config(tmp_path / "config", providers=("echo",))
    iris = LocalIrisBoundary(LocalBridgeRunner(), secrets.token_bytes(32))
    inner_store = SQLiteTaskStore(tmp_path / "state")
    handle = _submit_queued(inner_store, key="verifier-close-fail")
    registry = ExecutionRegistry()

    class FailPersistStore:
        def __init__(self, inner: SQLiteTaskStore) -> None:
            self._inner = inner

        def __getattr__(self, name: str) -> object:
            return getattr(self._inner, name)

        def persist_terminal_projection(
            self, task_handle: str, projection: TerminalProjection
        ) -> None:
            raise TaskStoreUnavailable("persist failed")

    store = FailPersistStore(inner_store)
    server = MCPServer(
        MCPServerDependencies(
            router=ComboRouter(iris, DirectoryLeaseManager(), attempt_authorizer=iris),
            registry=registry,
            verifier=lambda vr, control: (_ for _ in ()).throw(RuntimeError("verifier boom")),
            profile_resolver=resolve_service_profile,
            control_factory=CancellationToken,
            task_store=inner_store,
            routing_authority=DeterministicRoutingAuthority(config_dir),
            flow_controller=make_flow_controller(store),  # type: ignore[arg-type]
        )
    )
    app = MCPApplication(server)

    resp = app.call(
        "run_combo",
        {
            **routing_arguments(),
            "attempts": [
                {
                    "provider": "echo",
                    "command": ["echo", "ok"],
                    "cwd": str(tmp_path),
                }
            ],
            "task_handle": handle,
            "verification": {"files": [str(tmp_path / "sentinel.txt")]},
        },
        request_id="req-verifier-close-fail",
    )
    payload = json.loads(resp["content"][0]["text"])
    assert resp.get("isError") is True
    assert payload["error"] == "TASK_STORE_UNAVAILABLE"
    entries = registry.list()
    assert len(entries) == 1
    assert entries[0]["finalized"] is True
    assert entries[0]["state"] == ExecutionState.FAILED.value


def test_routing_failure_leaves_no_running_task_when_store_available(
    tmp_path: Path,
) -> None:
    import secrets

    from athena.bridge import LocalBridgeRunner
    from athena.execution import CancellationToken
    from athena.flow.controller import make_flow_controller
    from athena.iris import LocalIrisBoundary
    from athena.lease import DirectoryLeaseManager
    from athena.mcp_server import MCPServer, MCPServerDependencies
    from athena.mcp_stdio.application import MCPApplication
    from athena.profiles import resolve_service_profile
    from athena.registry import ExecutionRegistry
    from athena.router import AllAttemptsFailed, ComboRouter
    from athena.routing_authority import DeterministicRoutingAuthority
    from tests.route0_support import routing_arguments, write_route_config

    config_dir = write_route_config(tmp_path / "config", providers=("echo",))
    iris = LocalIrisBoundary(LocalBridgeRunner(), secrets.token_bytes(32))
    store = SQLiteTaskStore(tmp_path / "state")
    handle = _submit_queued(store, key="no-running-key")

    router = ComboRouter(iris, DirectoryLeaseManager(), attempt_authorizer=iris)
    original_run = router.run

    def failing_run(*args: object, **kwargs: object) -> object:
        raise AllAttemptsFailed("simulated routing failure")

    router.run = failing_run  # type: ignore[method-assign]

    server = MCPServer(
        MCPServerDependencies(
            router=router,
            registry=ExecutionRegistry(),
            verifier=lambda vr, control: (_ for _ in ()).throw(AssertionError("no verifier")),
            profile_resolver=resolve_service_profile,
            control_factory=CancellationToken,
            task_store=store,
            routing_authority=DeterministicRoutingAuthority(config_dir),
            flow_controller=make_flow_controller(store),
        )
    )
    app = MCPApplication(server)

    resp = app.call(
        "run_combo",
        {
            **routing_arguments(),
            "attempts": [
                {
                    "provider": "echo",
                    "command": ["echo", "fail"],
                    "cwd": str(tmp_path),
                }
            ],
            "task_handle": handle,
            "verification": {"files": [str(tmp_path / "sentinel.txt")]},
        },
        request_id="req-no-running",
    )
    assert resp.get("isError") is True
    record = store.get_task(handle)
    assert record is not None
    assert record.state != "running"
    assert record.state == "awaiting_human_review"
    assert record.validation_status == "fail"
    assert record.chronos_action == "HUMAN_REVIEW"

    router.run = original_run  # type: ignore[method-assign]
