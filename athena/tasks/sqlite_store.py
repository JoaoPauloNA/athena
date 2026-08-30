"""Implementação SQLite (stdlib) do armazenamento durável de tarefas.

Localização preguiçosa: nenhum diretório ou arquivo é tocado até a primeira
operação real (`submit_task`/`get_task`). `ATHENA_STATE_DIR` sobrepõe o
default `~/.athena/state` quando definida no momento da primeira operação.

Limitação documentada: `sqlite3.connect` da stdlib não aceita `O_NOFOLLOW`.
A rejeição de symlink/arquivo não regular é checar-então-usar
(`os.path.islink`/`os.stat`) imediatamente antes de conectar, não uma
transação POSIX atômica.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import sqlite3
import stat
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from .contracts import (
    DELIVERY_STATUS_AWAITING,
    STABLE_REASON_CODES,
    SubmitTaskResult,
    TaskHandleNotFound,
    TaskIdempotencyConflict,
    TaskNotExecutable,
    TaskRecord,
    TaskStoreUnavailable,
    TaskSubmission,
    TerminalProjection,
)

# v1 was the TASK-0 schema; v2 adds FLOW-1 projection columns.
SCHEMA_VERSION = 2
_IDEMPOTENCY_DOMAIN = b"athena.tasks.idempotency_key.v1:"
_DB_FILENAME = "tasks.sqlite3"
_STATE_DIR_ENV = "ATHENA_STATE_DIR"

_TERMINAL_STATES = frozenset({"awaiting_human_review"})
_NON_QUEUED_STATES = frozenset({"running"}) | _TERMINAL_STATES


def _default_state_dir() -> Path:
    return Path.home() / ".athena" / "state"


def resolve_state_dir(explicit: Path | str | None = None) -> Path:
    """Resolve a store location: explicit arg > ATHENA_STATE_DIR > lazy default."""
    if explicit is not None:
        return Path(explicit)
    override = os.environ.get(_STATE_DIR_ENV)
    if override:
        return Path(override)
    return _default_state_dir()


def _digest_idempotency_key(key: str) -> str:
    return sha256(_IDEMPOTENCY_DOMAIN + key.encode("utf-8")).hexdigest()


def _now_iso(clock: Callable[[], datetime]) -> str:
    return clock().astimezone(UTC).isoformat(timespec="microseconds")


def _sanitize_reason_codes(codes: tuple[str, ...]) -> tuple[str, ...]:
    """Keep only stable codes; replace unknown with sentinel, deduplicate."""
    result = []
    for code in codes:
        result.append(code if code in STABLE_REASON_CODES else "FLOW_STORE_ERROR")
    return tuple(dict.fromkeys(result)) or ("FLOW_STORE_ERROR",)


def _decode_public_reason_codes(raw_rc: str | None) -> tuple[str, ...] | None:
    """Decode DB reason_codes_json; corrupt values become a stable sentinel tuple."""
    if raw_rc is None:
        return None
    try:
        parsed = json.loads(raw_rc)
    except (ValueError, TypeError):
        return ("FLOW_STORE_ERROR",)
    if not isinstance(parsed, list) or not parsed:
        return ("FLOW_STORE_ERROR",)
    if not all(isinstance(code, str) and code for code in parsed):
        return ("FLOW_STORE_ERROR",)
    if not all(code in STABLE_REASON_CODES for code in parsed):
        return ("FLOW_STORE_ERROR",)
    return tuple(parsed)


class SQLiteTaskStore:
    """Store durável, thread-safe, sem estado mutável de nível de módulo."""

    def __init__(
        self,
        state_dir: Path | str | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        self._state_dir = resolve_state_dir(state_dir)
        self._db_path = self._state_dir / _DB_FILENAME
        self._clock = clock or (lambda: datetime.now(UTC))
        self._busy_timeout_ms = busy_timeout_ms
        self._lock = threading.Lock()

    @property
    def db_path(self) -> Path:
        return self._db_path

    def _prepare_state_dir(self) -> None:
        if self._state_dir.exists():
            if self._state_dir.is_symlink():
                raise TaskStoreUnavailable("state directory must not be a symlink")
            if not self._state_dir.is_dir():
                raise TaskStoreUnavailable("state path must be a directory")
        else:
            try:
                self._state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            except OSError as exc:
                raise TaskStoreUnavailable("failed to create state directory") from exc
        try:
            os.chmod(self._state_dir, 0o700)
        except OSError as exc:
            raise TaskStoreUnavailable("failed to secure state directory") from exc

    def _reject_unsafe_db_path(self) -> None:
        if self._db_path.exists() or self._db_path.is_symlink():
            if self._db_path.is_symlink():
                raise TaskStoreUnavailable("task database must not be a symlink")
            mode = os.stat(self._db_path, follow_symlinks=False).st_mode
            if not stat.S_ISREG(mode):
                raise TaskStoreUnavailable("task database must be a regular file")

    @contextlib.contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._prepare_state_dir()
            self._reject_unsafe_db_path()
            try:
                connection = sqlite3.connect(
                    self._db_path,
                    timeout=self._busy_timeout_ms / 1000,
                    isolation_level=None,
                )
            except sqlite3.Error as exc:
                raise TaskStoreUnavailable("failed to open task database") from exc
            try:
                connection.row_factory = sqlite3.Row
                connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA synchronous = FULL")
                connection.execute("PRAGMA foreign_keys = ON")
                try:
                    os.chmod(self._db_path, 0o600)
                except OSError as exc:
                    raise TaskStoreUnavailable("failed to secure task database") from exc
                self._ensure_schema(connection)
                yield connection
            except sqlite3.Error as exc:
                raise TaskStoreUnavailable("task database operation failed") from exc
            finally:
                connection.close()

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_meta (version INTEGER NOT NULL)"
        )
        row = connection.execute("SELECT version FROM schema_meta").fetchone()
        if row is None:
            self._create_v2_tables(connection)
            connection.execute(
                "INSERT INTO schema_meta (version) VALUES (?)", (SCHEMA_VERSION,)
            )
            return
        version = row["version"]
        if version == 1:
            self._migrate_v1_to_v2(connection)
            connection.execute(
                "UPDATE schema_meta SET version = ?", (SCHEMA_VERSION,)
            )
            return
        if version != SCHEMA_VERSION:
            raise TaskStoreUnavailable("unsupported task database schema version")

    def _create_v2_tables(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS tasks ("
            "task_handle TEXT PRIMARY KEY,"
            "idempotency_key_digest TEXT NOT NULL UNIQUE,"
            "task_hash TEXT NOT NULL,"
            "canonical_task_json TEXT NOT NULL,"
            "task_type TEXT NOT NULL,"
            "state TEXT NOT NULL,"
            "priority INTEGER NOT NULL,"
            "revision INTEGER NOT NULL,"
            "created_at TEXT NOT NULL,"
            "updated_at TEXT NOT NULL,"
            "execution_id TEXT,"
            "execution_status TEXT,"
            "validation_status TEXT,"
            "delivery_status TEXT,"
            "chronos_action TEXT,"
            "attempts_used INTEGER,"
            "reason_codes_json TEXT"
            ")"
        )

    def _migrate_v1_to_v2(self, connection: sqlite3.Connection) -> None:
        existing_cols = {
            row[1]
            for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
        }
        new_cols = [
            ("execution_id", "TEXT"),
            ("execution_status", "TEXT"),
            ("validation_status", "TEXT"),
            ("delivery_status", "TEXT"),
            ("chronos_action", "TEXT"),
            ("attempts_used", "INTEGER"),
            ("reason_codes_json", "TEXT"),
        ]
        for col_name, col_type in new_cols:
            if col_name not in existing_cols:
                connection.execute(
                    f"ALTER TABLE tasks ADD COLUMN {col_name} {col_type}"
                )

    def submit_task(self, submission: TaskSubmission) -> SubmitTaskResult:
        key_digest = _digest_idempotency_key(submission.idempotency_key)
        task_hash = sha256(submission.canonical_json.encode("utf-8")).hexdigest()

        with self._connect() as connection:
            row = connection.execute(
                "SELECT task_handle, task_hash, state, revision, created_at, updated_at "
                "FROM tasks WHERE idempotency_key_digest = ?",
                (key_digest,),
            ).fetchone()
            if row is not None:
                if row["task_hash"] != task_hash:
                    raise TaskIdempotencyConflict(
                        "idempotency_key already used with a different task"
                    )
                return SubmitTaskResult(
                    task_handle=row["task_handle"],
                    state=row["state"],
                    created=False,
                    revision=row["revision"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )

            handle = secrets.token_hex(16)
            now = _now_iso(self._clock)
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO tasks ("
                    "task_handle, idempotency_key_digest, task_hash, canonical_task_json,"
                    " task_type, state, priority, revision, created_at, updated_at"
                    ") VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        handle,
                        key_digest,
                        task_hash,
                        submission.canonical_json,
                        submission.task_type,
                        "queued",
                        submission.priority,
                        1,
                        now,
                        now,
                    ),
                )
                connection.execute("COMMIT")
            except sqlite3.IntegrityError:
                connection.execute("ROLLBACK")
                existing = connection.execute(
                    "SELECT task_handle, task_hash, state, revision, created_at, updated_at "
                    "FROM tasks WHERE idempotency_key_digest = ?",
                    (key_digest,),
                ).fetchone()
                if existing is None:
                    raise TaskStoreUnavailable(
                        "task database convergence failed"
                    ) from None
                if existing["task_hash"] != task_hash:
                    raise TaskIdempotencyConflict(
                        "idempotency_key already used with a different task"
                    ) from None
                return SubmitTaskResult(
                    task_handle=existing["task_handle"],
                    state=existing["state"],
                    created=False,
                    revision=existing["revision"],
                    created_at=existing["created_at"],
                    updated_at=existing["updated_at"],
                )
            return SubmitTaskResult(
                task_handle=handle,
                state="queued",
                created=True,
                revision=1,
                created_at=now,
                updated_at=now,
            )

    def get_task(self, task_handle: str) -> TaskRecord | None:
        if not isinstance(task_handle, str) or not task_handle:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT task_handle, task_type, state, priority, revision,"
                " created_at, updated_at,"
                " execution_id, execution_status, validation_status,"
                " delivery_status, chronos_action, attempts_used, reason_codes_json"
                " FROM tasks WHERE task_handle = ?",
                (task_handle,),
            ).fetchone()
        if row is None:
            return None
        reason_codes = _decode_public_reason_codes(row["reason_codes_json"])
        return TaskRecord(
            task_handle=row["task_handle"],
            task_type=row["task_type"],
            state=row["state"],
            priority=row["priority"],
            revision=row["revision"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            execution_id=row["execution_id"],
            execution_status=row["execution_status"],
            validation_status=row["validation_status"],
            delivery_status=row["delivery_status"],
            chronos_action=row["chronos_action"],
            attempts_used=row["attempts_used"],
            reason_codes=reason_codes,
        )

    def transition_queued_to_running(
        self, task_handle: str, execution_id: str
    ) -> None:
        """Atomic queued→running before any runner is invoked."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state FROM tasks WHERE task_handle = ?",
                (task_handle,),
            ).fetchone()
            if row is None:
                raise TaskHandleNotFound("task handle not found")
            current = row["state"]
            if current == "running":
                raise TaskNotExecutable("TASK_ALREADY_RUNNING")
            if current in _TERMINAL_STATES:
                raise TaskNotExecutable("TASK_ALREADY_TERMINAL")
            if current != "queued":
                raise TaskNotExecutable("TASK_NOT_EXECUTABLE")

            now = _now_iso(self._clock)
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                "UPDATE tasks SET state = 'running', execution_id = ?,"
                " revision = revision + 1, updated_at = ?"
                " WHERE task_handle = ? AND state = 'queued'",
                (execution_id, now, task_handle),
            ).rowcount
            if updated != 1:
                connection.execute("ROLLBACK")
                raise TaskNotExecutable("TASK_ALREADY_RUNNING")
            connection.execute("COMMIT")

    def persist_terminal_projection(
        self, task_handle: str, projection: TerminalProjection
    ) -> None:
        """Write sanitized terminal projection atomically.

        Requires state='running' AND matching execution_id.
        Rolls back and raises TaskStoreUnavailable on any mismatch.
        """
        # TerminalProjection.__post_init__ already validated all fields.
        rc_json = json.dumps(list(projection.reason_codes))
        with self._connect() as connection:
            now = _now_iso(self._clock)
            connection.execute("BEGIN IMMEDIATE")
            # Atomic guard: must be running AND same execution_id
            updated = connection.execute(
                "UPDATE tasks SET"
                " state = 'awaiting_human_review',"
                " execution_status = ?,"
                " validation_status = ?,"
                " delivery_status = ?,"
                " chronos_action = ?,"
                " attempts_used = ?,"
                " reason_codes_json = ?,"
                " revision = revision + 1,"
                " updated_at = ?"
                " WHERE task_handle = ? AND state = 'running'"
                " AND execution_id = ?",
                (
                    projection.execution_status,
                    projection.validation_status,
                    projection.delivery_status,
                    projection.chronos_action,
                    projection.attempts_used,
                    rc_json,
                    now,
                    task_handle,
                    projection.execution_id,
                ),
            ).rowcount
            if updated != 1:
                connection.execute("ROLLBACK")
                raise TaskStoreUnavailable(
                    "persist_terminal_projection: state mismatch or handle not found"
                )
            connection.execute("COMMIT")

    def close_failed(
        self, task_handle: str, execution_id: str, reason_codes: tuple[str, ...]
    ) -> None:
        """Durably close to awaiting_human_review after a runner/routing failure.

        Idempotent only when the same execution_id is already terminal.
        """
        safe_codes = _sanitize_reason_codes(reason_codes)
        rc_json = json.dumps(list(safe_codes))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state, execution_id FROM tasks WHERE task_handle = ?",
                (task_handle,),
            ).fetchone()
            if row is None:
                connection.execute("ROLLBACK")
                raise TaskStoreUnavailable("close_failed: handle not found")
            current_state = row["state"]
            stored_execution_id = row["execution_id"]
            if current_state in _TERMINAL_STATES:
                if stored_execution_id == execution_id:
                    connection.execute("ROLLBACK")
                    return
                connection.execute("ROLLBACK")
                raise TaskStoreUnavailable(
                    "close_failed: already terminal with different execution_id"
                )
            if current_state != "running":
                connection.execute("ROLLBACK")
                raise TaskStoreUnavailable("close_failed: task not running")
            if stored_execution_id != execution_id:
                connection.execute("ROLLBACK")
                raise TaskStoreUnavailable("close_failed: execution_id mismatch")
            now = _now_iso(self._clock)
            updated = connection.execute(
                "UPDATE tasks SET"
                " state = 'awaiting_human_review',"
                " execution_status = 'failed',"
                " validation_status = 'fail',"
                " delivery_status = ?,"
                " chronos_action = 'HUMAN_REVIEW',"
                " attempts_used = 1,"
                " reason_codes_json = ?,"
                " revision = revision + 1,"
                " updated_at = ?"
                " WHERE task_handle = ? AND state = 'running'"
                " AND execution_id = ?",
                (
                    DELIVERY_STATUS_AWAITING,
                    rc_json,
                    now,
                    task_handle,
                    execution_id,
                ),
            ).rowcount
            if updated != 1:
                connection.execute("ROLLBACK")
                raise TaskStoreUnavailable("close_failed: update did not apply")
            connection.execute("COMMIT")
