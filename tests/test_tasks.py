"""Testes focados de TASK-0: submissão de tarefa durável e idempotente."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from athena.tasks import (
    SQLiteTaskStore,
    TaskIdempotencyConflict,
    TaskStoreUnavailable,
    TaskValidationError,
    build_submission,
)
from athena.tasks.sqlite_store import resolve_state_dir


def _task(**overrides: object) -> dict[str, object]:
    task: dict[str, object] = {"task_type": "demo.task", "input": "hello"}
    task.update(overrides)
    return task


# ---------------------------------------------------------------------------
# build_submission validation
# ---------------------------------------------------------------------------


def test_build_submission_accepts_minimal_task() -> None:
    submission = build_submission("key-1", _task())
    assert submission.idempotency_key == "key-1"
    assert submission.task_type == "demo.task"
    assert submission.priority == 5


@pytest.mark.parametrize(
    ("idempotency_key", "task", "expected_code"),
    [
        ("", _task(), "INVALID_TASK"),
        ("k" * 300, _task(), "TASK_TOO_LARGE"),
        ("é" * 129, _task(), "TASK_TOO_LARGE"),
        ("key", {"task_type": "Invalid Type", "input": "x"}, "INVALID_TASK"),
        ("key", {"task_type": "demo", "input": "x" * (33 * 1024)}, "TASK_TOO_LARGE"),
        ("key", {"task_type": "demo", "input": "x", "unexpected": 1}, "INVALID_TASK"),
        ("key", {"task_type": "demo", "input": "x", "priority": 10}, "INVALID_TASK"),
        (
            "key",
            {"task_type": "demo", "input": "x", "constraints": {"password": "abc"}},
            "INVALID_TASK",
        ),
        (
            "key",
            {"task_type": "demo", "input": "x", "constraints": {"a": float("nan")}},
            "INVALID_TASK",
        ),
        (
            "key",
            {"task_type": "demo", "input": "x", "constraints": {"a": float("inf")}},
            "INVALID_TASK",
        ),
    ],
)
def test_build_submission_rejects_invalid_variants(
    idempotency_key: str, task: dict[str, object], expected_code: str
) -> None:
    with pytest.raises(TaskValidationError) as excinfo:
        build_submission(idempotency_key, task)
    assert excinfo.value.code == expected_code


def test_build_submission_rejects_excess_depth() -> None:
    nested: dict[str, object] = {"leaf": 1}
    for _ in range(40):
        nested = {"nested": nested}
    with pytest.raises(TaskValidationError) as excinfo:
        build_submission("key", _task(constraints=nested))
    assert excinfo.value.code == "INVALID_TASK"


def test_build_submission_rejects_excess_items() -> None:
    huge = {f"key{i}": i for i in range(10_001)}
    with pytest.raises(TaskValidationError) as excinfo:
        build_submission("key", _task(constraints=huge))
    assert excinfo.value.code == "TASK_TOO_LARGE"


def test_build_submission_error_never_echoes_input_value() -> None:
    marker = "super-secret-marker-value-should-not-leak"
    with pytest.raises(TaskValidationError) as excinfo:
        build_submission("key", {"task_type": "Bad Type", "input": marker})
    assert marker not in str(excinfo.value)


# ---------------------------------------------------------------------------
# SQLiteTaskStore behaviour
# ---------------------------------------------------------------------------


def test_lazy_construction_touches_no_filesystem(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    SQLiteTaskStore(state_dir)
    assert not state_dir.exists()


def test_first_submit_creates_queued_task(tmp_path: Path) -> None:
    store = SQLiteTaskStore(tmp_path / "state")
    submission = build_submission("key-1", _task())
    result = store.submit_task(submission)
    assert result.created is True
    assert result.state == "queued"
    assert result.revision == 1
    assert result.created_at == result.updated_at


def test_exact_replay_returns_same_handle_created_false(tmp_path: Path) -> None:
    store = SQLiteTaskStore(tmp_path / "state")
    submission = build_submission("key-1", _task())
    first = store.submit_task(submission)
    second = store.submit_task(submission)
    assert second.created is False
    assert second.task_handle == first.task_handle
    assert second.revision == first.revision


def test_same_key_different_task_conflicts(tmp_path: Path) -> None:
    store = SQLiteTaskStore(tmp_path / "state")
    store.submit_task(build_submission("key-1", _task()))
    with pytest.raises(TaskIdempotencyConflict):
        store.submit_task(build_submission("key-1", _task(input="different")))


def test_concurrent_same_key_submissions_converge_to_one_handle(tmp_path: Path) -> None:
    store = SQLiteTaskStore(tmp_path / "state")
    submission = build_submission("key-concurrent", _task())

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.submit_task(submission), range(8)))

    handles = {result.task_handle for result in results}
    assert len(handles) == 1
    assert sum(1 for result in results if result.created) == 1


def test_get_task_returns_sanitized_projection_only(tmp_path: Path) -> None:
    store = SQLiteTaskStore(tmp_path / "state")
    submission = build_submission("key-1", _task(project_ref="proj-x"))
    submitted = store.submit_task(submission)

    record = store.get_task(submitted.task_handle)
    assert record is not None
    assert record.task_handle == submitted.task_handle
    assert record.task_type == "demo.task"
    assert record.state == "queued"
    assert record.priority == 5
    assert record.revision == 1
    fields = {
        "task_handle",
        "task_type",
        "state",
        "priority",
        "revision",
        "created_at",
        "updated_at",
        "execution_id",
        "execution_status",
        "validation_status",
        "delivery_status",
        "chronos_action",
        "attempts_used",
        "reason_codes",
    }
    assert set(record.__dataclass_fields__) == fields  # type: ignore[attr-defined]


def test_get_task_unknown_handle_is_safe(tmp_path: Path) -> None:
    store = SQLiteTaskStore(tmp_path / "state")
    assert store.get_task("does-not-exist") is None
    assert store.get_task("") is None


def test_raw_idempotency_key_absent_from_database_bytes(tmp_path: Path) -> None:
    store = SQLiteTaskStore(tmp_path / "state")
    secret_key = "raw-idempotency-key-should-never-be-stored-plainly"
    store.submit_task(build_submission(secret_key, _task()))

    raw_bytes = store.db_path.read_bytes()
    assert secret_key.encode("utf-8") not in raw_bytes


def test_state_dir_and_db_file_permissions(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    store = SQLiteTaskStore(state_dir)
    store.submit_task(build_submission("key-1", _task()))

    dir_mode = stat.S_IMODE(state_dir.stat().st_mode)
    db_mode = stat.S_IMODE(store.db_path.stat().st_mode)
    assert dir_mode == 0o700
    assert db_mode == 0o600


def test_symlinked_state_dir_is_rejected(tmp_path: Path) -> None:
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    link_dir = tmp_path / "link"
    link_dir.symlink_to(real_dir, target_is_directory=True)

    store = SQLiteTaskStore(link_dir)
    with pytest.raises(TaskStoreUnavailable):
        store.submit_task(build_submission("key-1", _task()))


def test_symlinked_db_file_is_rejected(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o700)
    target = tmp_path / "elsewhere.sqlite3"
    target.write_bytes(b"")
    db_link = state_dir / "tasks.sqlite3"
    db_link.symlink_to(target)

    store = SQLiteTaskStore(state_dir)
    with pytest.raises(TaskStoreUnavailable):
        store.submit_task(build_submission("key-1", _task()))


def test_non_regular_db_path_is_rejected(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o700)
    fifo_path = state_dir / "tasks.sqlite3"
    os.mkfifo(fifo_path)

    store = SQLiteTaskStore(state_dir)
    with pytest.raises(TaskStoreUnavailable):
        store.submit_task(build_submission("key-1", _task()))


def test_no_orphan_or_temp_residue(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    store = SQLiteTaskStore(state_dir)
    store.submit_task(build_submission("key-1", _task()))
    store.get_task("does-not-exist")

    residue = {path.name for path in state_dir.iterdir()}
    allowed = {"tasks.sqlite3", "tasks.sqlite3-wal", "tasks.sqlite3-shm"}
    assert residue <= allowed


def test_resolve_state_dir_prefers_explicit_then_env_then_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    explicit = tmp_path / "explicit"
    assert resolve_state_dir(explicit) == explicit

    monkeypatch.setenv("ATHENA_STATE_DIR", str(tmp_path / "from-env"))
    assert resolve_state_dir(None) == tmp_path / "from-env"

    monkeypatch.delenv("ATHENA_STATE_DIR", raising=False)
    assert resolve_state_dir(None) == Path.home() / ".athena" / "state"


# ---------------------------------------------------------------------------
# Real two-process restart smoke test
# ---------------------------------------------------------------------------


def _run_athena_requests(
    state_dir: Path, requests: tuple[dict[str, object], ...]
) -> list[dict[str, object]]:
    env = dict(os.environ)
    env["ATHENA_STATE_DIR"] = str(state_dir)
    process = subprocess.run(
        [sys.executable, "-m", "athena"],
        cwd=Path(__file__).resolve().parents[1],
        input="".join(json.dumps(item) + "\n" for item in requests),
        text=True,
        capture_output=True,
        timeout=15,
        env=env,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    return [json.loads(line) for line in process.stdout.splitlines() if line]


def test_two_process_restart_recovers_queued_task(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"

    submit_requests = (
        {"jsonrpc": "2.0", "id": "submit", "method": "tools/call", "params": {
            "name": "submit_task",
            "arguments": {
                "idempotency_key": "restart-key",
                "task": {"task_type": "demo.task", "input": "restart-input"},
            },
        }},
    )
    submit_responses = _run_athena_requests(state_dir, submit_requests)
    submit_payload = json.loads(submit_responses[0]["result"]["content"][0]["text"])
    assert submit_payload["created"] is True
    handle = submit_payload["task_handle"]

    get_requests = (
        {"jsonrpc": "2.0", "id": "get", "method": "tools/call", "params": {
            "name": "get_task",
            "arguments": {"task_handle": handle},
        }},
    )
    get_responses = _run_athena_requests(state_dir, get_requests)
    get_payload = json.loads(get_responses[0]["result"]["content"][0]["text"])
    assert get_payload["found"] is True
    assert get_payload["task_handle"] == handle
    assert get_payload["state"] == "queued"


def test_ping_and_tools_list_create_no_database(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    requests = (
        {"jsonrpc": "2.0", "id": "ping", "method": "ping"},
        {"jsonrpc": "2.0", "id": "tools", "method": "tools/list"},
    )
    responses = _run_athena_requests(state_dir, requests)
    by_id = {item["id"]: item for item in responses}
    assert by_id["ping"]["result"] == {}
    names = {tool["name"] for tool in by_id["tools"]["result"]["tools"]}
    assert {"submit_task", "get_task"} <= names
    assert not state_dir.exists()
