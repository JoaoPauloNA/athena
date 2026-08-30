"""Persistência SQLite local para eventos Clio — append-only com retenção."""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import stat
import threading
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .contracts import LEVEL_NONE, RETENTION_DAYS

_STATE_DIR_ENV = "ATHENA_STATE_DIR"
_DB_FILENAME = "clio.sqlite3"


def _default_state_dir() -> Path:
    return Path.home() / ".athena" / "state"


def resolve_state_dir(explicit: Path | str | None = None) -> Path:
    if explicit is not None:
        return Path(explicit)
    override = os.environ.get(_STATE_DIR_ENV)
    if override:
        return Path(override)
    return _default_state_dir()


class ClioStore:
    """Store append-only com retenção por nível — lazy, thread-safe."""

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
        self._initialized = False

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def state_dir(self) -> Path:
        return self._state_dir

    def _prepare_state_dir(self) -> None:
        if self._state_dir.exists():
            if self._state_dir.is_symlink():
                raise OSError("state directory must not be a symlink")
            if not self._state_dir.is_dir():
                raise OSError("state path must be a directory")
        else:
            self._state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self._state_dir, 0o700)

    def _reject_unsafe_db_path(self) -> None:
        if self._db_path.exists() or self._db_path.is_symlink():
            if self._db_path.is_symlink():
                raise OSError("clio database must not be a symlink")
            mode = os.stat(self._db_path, follow_symlinks=False).st_mode
            if not stat.S_ISREG(mode):
                raise OSError("clio database must be a regular file")

    @contextlib.contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._prepare_state_dir()
            self._reject_unsafe_db_path()
            connection = sqlite3.connect(
                self._db_path,
                timeout=self._busy_timeout_ms / 1000,
                isolation_level=None,
            )
            try:
                connection.row_factory = sqlite3.Row
                connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA synchronous = FULL")
                connection.execute("PRAGMA foreign_keys = ON")
                os.chmod(self._db_path, 0o600)
                self._ensure_schema(connection)
                self._initialized = True
                yield connection
            finally:
                connection.close()

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS clio_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                schema_version TEXT NOT NULL,
                event_type TEXT NOT NULL,
                level TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_clio_events_created ON clio_events(created_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_clio_events_level ON clio_events(level)"
        )

    def insert_batch(self, rows: Sequence[tuple[str, str, str, str, str]]) -> int:
        """Inserir lote: (schema_version, event_type, level, timestamp, payload_json)."""
        if not rows:
            return 0
        created_at = self._clock().astimezone(UTC).isoformat(timespec="microseconds")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.executemany(
                    """
                    INSERT INTO clio_events
                        (schema_version, event_type, level, timestamp, payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            schema_version,
                            event_type,
                            level,
                            timestamp,
                            payload_json,
                            created_at,
                        )
                        for schema_version, event_type, level, timestamp, payload_json in rows
                    ],
                )
                connection.execute("COMMIT")
            except sqlite3.Error:
                connection.execute("ROLLBACK")
                raise
        return len(rows)

    def apply_retention(self) -> int:
        """Remover registros vencidos por nível; retorna contagem removida."""
        now = self._clock().astimezone(UTC)
        removed = 0
        with self._connect() as connection:
            for level, days in RETENTION_DAYS.items():
                if level == LEVEL_NONE or days <= 0:
                    continue
                cutoff = (now - timedelta(days=days)).isoformat(timespec="microseconds")
                cursor = connection.execute(
                    "DELETE FROM clio_events WHERE level = ? AND created_at < ?",
                    (level, cutoff),
                )
                removed += cursor.rowcount
        return removed

    def count_events(self) -> int:
        if not self._db_path.exists():
            return 0
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS c FROM clio_events").fetchone()
            return int(row["c"]) if row else 0

    def list_event_types(self) -> list[str]:
        if not self._db_path.exists():
            return []
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT event_type FROM clio_events ORDER BY event_type"
            ).fetchall()
            return [str(row["event_type"]) for row in rows]

    @staticmethod
    def decode_row_payload(payload_json: str) -> dict:
        parsed = json.loads(payload_json)
        if not isinstance(parsed, dict):
            raise TypeError("invalid stored payload")
        return parsed
