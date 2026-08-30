"""Writer em background — lotes, retenção e shutdown limitado."""

from __future__ import annotations

import json
import sqlite3
import threading
import time

from .contracts import (
    BATCH_SIZE,
    FLUSH_INTERVAL_S,
    MAX_FLUSH_EVENTS,
    SHUTDOWN_TIMEOUT_S,
    WRITER_JOIN_TIMEOUT_S,
    MutableClioCounters,
)
from .producer import ClioProducer
from .store import ClioStore


class ClioWriter:
    """Consome fila do produtor e persiste lotes fora do caminho quente."""

    def __init__(
        self,
        producer: ClioProducer,
        store: ClioStore,
        *,
        counters: MutableClioCounters | None = None,
        batch_size: int = BATCH_SIZE,
        flush_interval_s: float = FLUSH_INTERVAL_S,
    ) -> None:
        self._producer = producer
        self._store = store
        self._counters = counters or producer.counters
        self._batch_size = batch_size
        self._flush_interval_s = flush_interval_s
        self._stop = threading.Event()
        self._flush_requested = threading.Event()
        self._flush_done = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = False
        self._lock = threading.Lock()

    @property
    def started(self) -> bool:
        with self._lock:
            return self._started

    @property
    def producer(self) -> ClioProducer:
        return self._producer

    def thread_alive(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._stop.clear()
            self._flush_requested.clear()
            self._flush_done.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="clio-writer",
                daemon=True,
            )
            self._thread.start()
            self._started = True

    def _run(self) -> None:
        pending_flush = 0
        try:
            while not self._stop.is_set():
                try:
                    if self._flush_requested.is_set():
                        self._flush_requested.clear()
                        self._flush_remaining()
                        self._flush_done.set()
                        pending_flush = 0
                        continue
                    if pending_flush >= MAX_FLUSH_EVENTS:
                        self._flush_batch(self._batch_size)
                        pending_flush = 0
                    batch = self._producer.drain_nowait(self._batch_size)
                    if batch:
                        self._persist_batch(batch)
                        pending_flush += len(batch)
                    else:
                        pending_flush = 0
                        self._stop.wait(self._flush_interval_s)
                except Exception:  # noqa: BLE001 — contenção na fronteira background
                    self._counters.writer_failures += 1
                    self._stop.wait(self._flush_interval_s)
            try:
                self._flush_remaining()
            except Exception:  # noqa: BLE001
                self._counters.writer_failures += 1
                self._drop_remaining_bounded()
        finally:
            with self._lock:
                self._started = False

    def _flush_batch(self, limit: int) -> None:
        batch = self._producer.drain_nowait(limit)
        if batch:
            self._persist_batch(batch)

    def _flush_remaining(self) -> None:
        total = 0
        while total < MAX_FLUSH_EVENTS:
            batch = self._producer.drain_nowait(self._batch_size)
            if not batch:
                break
            self._persist_batch(batch)
            total += len(batch)

    def _drop_remaining_bounded(self) -> None:
        total = 0
        while total < MAX_FLUSH_EVENTS:
            batch = self._producer.drain_nowait(self._batch_size)
            if not batch:
                break
            self._counters.writer_failures += len(batch)
            total += len(batch)

    def _persist_batch(self, batch: list[tuple[bytes, dict[str, str]]]) -> None:
        rows: list[tuple[str, str, str, str, str]] = []
        for encoded, meta in batch:
            try:
                payload = json.loads(encoded.decode("utf-8"))
            except (ValueError, UnicodeError):
                self._counters.dropped_invalid += 1
                continue
            if not isinstance(payload, dict):
                self._counters.dropped_invalid += 1
                continue
            rows.append(
                (
                    meta.get("schema_version", ""),
                    meta.get("event_type", ""),
                    meta.get("level", ""),
                    meta.get("timestamp", ""),
                    json.dumps(payload, separators=(",", ":"), sort_keys=True),
                )
            )
        if not rows:
            return
        try:
            self._store.insert_batch(rows)
        except (OSError, sqlite3.Error):
            self._counters.writer_failures += len(rows)
            return
        try:
            self._store.apply_retention()
        except (OSError, sqlite3.Error):
            self._counters.retention_failures += 1

    def shutdown(self, *, timeout_s: float = SHUTDOWN_TIMEOUT_S) -> bool:
        """Shutdown limitado — nunca drena concorrentemente com o writer vivo.

        Returns True when the writer thread joined; False on bounded timeout.
        """
        self._stop.set()
        self._flush_requested.set()
        thread = self._thread
        join_budget = min(timeout_s, WRITER_JOIN_TIMEOUT_S)
        joined = True
        if thread is not None and thread.is_alive():
            thread.join(timeout=join_budget)
            joined = not thread.is_alive()
        if not joined:
            self._counters.writer_failures += 1
            return False
        return True

    def join_thread(self, *, timeout_s: float = WRITER_JOIN_TIMEOUT_S) -> bool:
        thread = self._thread
        if thread is None or not thread.is_alive():
            return True
        thread.join(timeout=timeout_s)
        return not thread.is_alive()

    def flush_sync(self) -> None:
        """Solicita flush ao writer e aguarda confirmação de persistência."""
        if not self.started or self._thread is None or not self._thread.is_alive():
            return
        self._flush_done.clear()
        self._flush_requested.set()
        deadline = time.monotonic() + SHUTDOWN_TIMEOUT_S
        while time.monotonic() < deadline:
            if self._flush_done.wait(timeout=0.01):
                return
            if not self._thread.is_alive():
                return
