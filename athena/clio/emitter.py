"""Emissor Clio — marcos FLOW-1 e execução sem autoridade operacional."""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from .contracts import (
    LEVEL_COMPLETE,
    LEVEL_NONE,
    LEVEL_PARTIAL,
    LEVEL_TECHNICAL,
    MAX_RETIRED_GENERATIONS,
    SHUTDOWN_TIMEOUT_S,
    ClioCounters,
    ContentProtectorContract,
    LevelContext,
    MutableClioCounters,
    PartialSummaries,
    TechnicalEvent,
)
from .policy import complete_content_allowed, resolve_level
from .producer import ClioProducer
from .sanitizer import (
    build_complete_payload,
    build_partial_payload,
    build_technical_payload,
    redact_text,
)
from .store import ClioStore
from .writer import ClioWriter


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


class ClioEmitter:
    """Observabilidade local não bloqueante — nunca altera vereditos FLOW."""

    def __init__(
        self,
        *,
        level_context: LevelContext | None = None,
        env: Mapping[str, str] | None = None,
        protector: ContentProtectorContract | None = None,
        state_dir: object | None = None,
        counters: MutableClioCounters | None = None,
    ) -> None:
        self._env = env
        self._level_context = level_context or LevelContext()
        self._protector = protector
        self._counters = counters or MutableClioCounters()
        self._resolved_level = resolve_level(self._level_context, env=env)
        self._store = ClioStore(state_dir) if state_dir is not None else ClioStore()
        self._producer: ClioProducer | None = None
        self._writer: ClioWriter | None = None
        self._retired_generations: list[tuple[ClioWriter, ClioProducer]] = []
        self._writer_lock = threading.Lock()
        self._previous_level = self._resolved_level
        if self._resolved_level != LEVEL_NONE:
            self._activate_generation()

    @property
    def active(self) -> bool:
        return self._resolved_level != LEVEL_NONE

    @property
    def level(self) -> str:
        return self._resolved_level

    @property
    def counters(self) -> ClioCounters:
        return self._counters.snapshot()

    @property
    def store(self) -> ClioStore:
        return self._store

    def _prune_retired_generations(self) -> None:
        self._retired_generations = [
            (writer, producer)
            for writer, producer in self._retired_generations
            if writer.thread_alive()
        ]

    def _live_retired_count(self) -> int:
        self._prune_retired_generations()
        return len(self._retired_generations)

    def _at_retired_capacity(self) -> bool:
        return self._live_retired_count() >= MAX_RETIRED_GENERATIONS

    def _activate_generation(self) -> ClioWriter | None:
        with self._writer_lock:
            if self._writer is not None and self._writer.started:
                return self._writer
            if self._at_retired_capacity():
                return None
            producer = ClioProducer(level=self._resolved_level, counters=self._counters)
            writer = ClioWriter(producer, self._store, counters=self._counters)
            writer.start()
            self._producer = producer
            self._writer = writer
            return writer

    def _shutdown_active_writer(self, *, timeout_s: float = SHUTDOWN_TIMEOUT_S) -> None:
        with self._writer_lock:
            writer = self._writer
            producer = self._producer
            if writer is None:
                return
            self._writer = None
            self._producer = None
            joined = writer.shutdown(timeout_s=timeout_s)
            if not joined and producer is not None:
                self._prune_retired_generations()
                if len(self._retired_generations) < MAX_RETIRED_GENERATIONS:
                    self._retired_generations.append((writer, producer))

    def _emit(self, event: TechnicalEvent, *, partial: PartialSummaries | None = None) -> None:
        if self._resolved_level == LEVEL_NONE:
            self._counters.none_bypass += 1
            return
        if self._resolved_level == LEVEL_COMPLETE and self._protector is None:
            self._counters.dropped_complete_unavailable += 1
            try:
                payload = build_technical_payload(event, level=LEVEL_TECHNICAL)
            except (ValueError, TypeError):
                self._counters.dropped_invalid += 1
                return
            self._enqueue(payload)
            return
        emission = self._resolved_level
        try:
            if emission == LEVEL_COMPLETE and complete_content_allowed(
                self._resolved_level, protector_available=self._protector is not None
            ):
                if partial is None or self._protector is None:
                    self._counters.dropped_complete_unavailable += 1
                    payload = build_technical_payload(event, level=LEVEL_TECHNICAL)
                else:
                    combined = " ".join(
                        filter(
                            None,
                            (
                                partial.request_summary,
                                partial.result_summary,
                            ),
                        )
                    )
                    envelope = self._protector.protect(combined.encode("utf-8"))
                    payload = build_complete_payload(event, envelope)
            elif emission == LEVEL_PARTIAL and partial is not None:
                payload = build_partial_payload(event, partial)
            else:
                payload = build_technical_payload(event, level=emission)
        except (ValueError, TypeError):
            self._counters.dropped_invalid += 1
            return
        self._enqueue(payload)

    def _enqueue(self, payload: dict[str, Any]) -> None:
        if self._resolved_level == LEVEL_NONE:
            self._counters.none_bypass += 1
            return
        producer = self._producer
        if producer is None or self._writer is None or not self._writer.started:
            writer = self._activate_generation()
            if writer is None:
                self._counters.dropped_writer_capacity += 1
                return
            producer = self._producer
        if producer is not None:
            producer.enqueue(payload)

    def refresh_level(self, context: LevelContext | None = None) -> None:
        """Recalcular nível; emite evento técnico se mudou."""
        if context is not None:
            self._level_context = context
        new_level = resolve_level(self._level_context, env=self._env)
        if new_level == self._previous_level:
            self._resolved_level = new_level
            if self._producer is not None:
                self._producer.set_level(new_level, old_level=self._previous_level)
            return
        old = self._previous_level
        if old != LEVEL_NONE:
            self._shutdown_active_writer(timeout_s=SHUTDOWN_TIMEOUT_S)
        self._resolved_level = new_level
        if new_level == LEVEL_NONE:
            self._previous_level = new_level
            return
        self._activate_generation()
        self._emit(
            TechnicalEvent(
                event_type="clio.level_changed",
                timestamp=_now_iso(),
                old_level=old,
                new_level=new_level,
            )
        )
        self._previous_level = new_level

    def emit_flow_started(
        self,
        *,
        task_handle: str,
        execution_id: str,
        tool: str,
    ) -> None:
        if self._resolved_level == LEVEL_NONE:
            self._counters.none_bypass += 1
            return
        self._emit(
            TechnicalEvent(
                event_type="flow.task.started",
                timestamp=_now_iso(),
                task_handle=task_handle,
                execution_id=execution_id,
                tool=tool,
            )
        )

    def emit_flow_finished(
        self,
        *,
        task_handle: str,
        execution_id: str,
        tool: str,
        flow_payload: Mapping[str, Any],
        execution_result: Any | None = None,
    ) -> None:
        if self._resolved_level == LEVEL_NONE:
            self._counters.none_bypass += 1
            return
        duration_ms = None
        provider = ""
        execution_status = ""
        if execution_result is not None:
            try:
                state = getattr(execution_result, "state", None)
                if state is not None:
                    execution_status = str(getattr(state, "value", state))
            except Exception:  # noqa: BLE001, S110
                pass
            try:
                duration_s = getattr(execution_result, "duration_s", None)
                if duration_s is not None:
                    duration_ms = int(float(duration_s) * 1000)
            except (TypeError, ValueError):
                duration_ms = None
            provider = str(getattr(execution_result, "provider", "") or "")

        partial = None
        if self._resolved_level in {LEVEL_PARTIAL, LEVEL_COMPLETE}:
            partial = PartialSummaries(
                request_summary=redact_text(f"task:{task_handle[:16]}"),
                result_summary=redact_text(
                    f"status:{flow_payload.get('validation_status', '')}"
                ),
                decision_summary=redact_text(
                    f"chronos:{flow_payload.get('chronos_action', '')}"
                ),
            )

        self._emit(
            TechnicalEvent(
                event_type="flow.task.finished",
                timestamp=_now_iso(),
                task_handle=task_handle,
                execution_id=execution_id,
                tool=tool,
                provider=provider,
                execution_status=execution_status,
                validation_status=str(flow_payload.get("validation_status", "")),
                delivery_status=str(flow_payload.get("delivery_status", "")),
                chronos_action=str(flow_payload.get("chronos_action", "")),
                attempts_used=(
                    int(flow_payload["attempts_used"])
                    if isinstance(flow_payload.get("attempts_used"), int)
                    else None
                ),
                duration_ms=duration_ms,
                reason_codes=tuple(flow_payload.get("reason_codes") or ()),
            ),
            partial=partial,
        )

    def shutdown(self, *, timeout_s: float = SHUTDOWN_TIMEOUT_S) -> None:
        deadline = time.monotonic() + timeout_s
        remaining = deadline - time.monotonic()
        if remaining > 0:
            self._shutdown_active_writer(timeout_s=remaining)
        retired = list(self._retired_generations)
        self._retired_generations.clear()
        for writer, _producer in retired:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            writer.join_thread(timeout_s=remaining)

    def flush_for_tests(self) -> None:
        writer = self._writer
        if writer is not None:
            writer.flush_sync()


def build_clio_emitter(
    *,
    level_context: LevelContext | None = None,
    env: Mapping[str, str] | None = None,
    protector: ContentProtectorContract | None = None,
    state_dir: object | None = None,
) -> ClioEmitter | None:
    """Factory de composição — retorna None quando nível efetivo é none."""
    resolved = resolve_level(level_context or LevelContext(), env=env)
    if resolved == LEVEL_NONE:
        return None
    return ClioEmitter(
        level_context=level_context,
        env=env,
        protector=protector,
        state_dir=state_dir,
    )
