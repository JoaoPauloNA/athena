"""Produtor não bloqueante — fila limitada e enqueue O(1)."""

from __future__ import annotations

import queue
import threading
from typing import Any

from .contracts import (
    LEVEL_NONE,
    QUEUE_CAPACITY,
    MutableClioCounters,
)
from .sanitizer import serialize_event


class ClioProducer:
    """Valida, serializa e enfileira eventos sem I/O de storage."""

    def __init__(
        self,
        *,
        level: str,
        counters: MutableClioCounters | None = None,
        queue_capacity: int = QUEUE_CAPACITY,
    ) -> None:
        self._level = level
        self._counters = counters or MutableClioCounters()
        self._queue: queue.Queue[tuple[bytes, dict[str, str]]] | None = None
        self._queue_capacity = queue_capacity
        self._started = False
        self._lock = threading.Lock()

    @property
    def level(self) -> str:
        return self._level

    @property
    def counters(self) -> MutableClioCounters:
        return self._counters

    @property
    def queue(self) -> queue.Queue[tuple[bytes, dict[str, str]]] | None:
        return self._queue

    def set_level(self, new_level: str, *, old_level: str | None = None) -> None:
        with self._lock:
            if old_level is None:
                old_level = self._level
            self._level = new_level

    def _ensure_queue(self) -> queue.Queue[tuple[bytes, dict[str, str]]]:
        if self._level == LEVEL_NONE:
            self._counters.none_bypass += 1
            raise RuntimeError("none level bypass")
        with self._lock:
            if self._queue is None:
                self._queue = queue.Queue(maxsize=self._queue_capacity)
                self._started = True
            return self._queue

    def enqueue(self, payload: dict[str, Any]) -> bool:
        """Enfileirar evento validado; nunca bloqueia o chamador."""
        if self._level == LEVEL_NONE:
            self._counters.none_bypass += 1
            return False
        try:
            encoded = serialize_event(payload)
        except (ValueError, TypeError):
            self._counters.dropped_invalid += 1
            return False
        try:
            event_queue = self._ensure_queue()
        except RuntimeError:
            return False
        meta = {
            "event_type": str(payload.get("event_type", "")),
            "level": str(payload.get("level", "")),
            "timestamp": str(payload.get("timestamp", "")),
            "schema_version": str(payload.get("schema_version", "")),
        }
        try:
            event_queue.put_nowait((encoded, meta))
        except queue.Full:
            self._counters.dropped_queue_full += 1
            return False
        self._counters.enqueued += 1
        return True

    def drain_nowait(self, limit: int) -> list[tuple[bytes, dict[str, str]]]:
        if self._queue is None:
            return []
        batch: list[tuple[bytes, dict[str, str]]] = []
        while len(batch) < limit:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return batch
