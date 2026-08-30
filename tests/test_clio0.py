"""Testes focados CLIO-0 — observabilidade não bloqueante."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from athena.clio import (
    LEVEL_COMPLETE,
    LEVEL_NONE,
    LEVEL_PARTIAL,
    LEVEL_TECHNICAL,
    ClioEmitter,
    LevelContext,
    ProtectedEnvelope,
    build_clio_emitter,
    resolve_level,
)
from athena.clio.contracts import (
    FORBIDDEN_FIELD_NAMES,
    MAX_RETIRED_GENERATIONS,
    SHUTDOWN_TIMEOUT_S,
    MutableClioCounters,
    TechnicalEvent,
)
from athena.clio.policy import complete_content_allowed
from athena.clio.producer import ClioProducer
from athena.clio.sanitizer import (
    build_technical_payload,
    normalize_error_code,
    normalize_identifier,
    normalize_timestamp,
    redact_text,
    serialize_event,
    validate_payload,
)
from athena.clio.store import ClioStore
from athena.clio.writer import ClioWriter
from harness import benchmark_clio as clio_benchmark


class _StubProtector:
    def protect(self, plaintext: bytes) -> ProtectedEnvelope:
        return ProtectedEnvelope(
            algorithm="stub-v1",
            payload_b64=plaintext.hex()[:64],
            key_id="test-key",
        )


def _technical_event(**overrides: object) -> TechnicalEvent:
    base = {
        "event_type": "flow.task.started",
        "timestamp": "2026-08-29T12:00:00+00:00",
        "task_handle": "th-001",
        "execution_id": "ex-001",
        "tool": "run_combo",
    }
    base.update(overrides)
    return TechnicalEvent(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Policy and precedence
# ---------------------------------------------------------------------------


def test_mcp_suggestion_cannot_elevate_above_global_policy() -> None:
    env = {"ATHENA_CLIO_LEVEL": LEVEL_TECHNICAL}
    ctx = LevelContext(mcp_suggestion=LEVEL_COMPLETE)
    assert resolve_level(ctx, env=env) == LEVEL_TECHNICAL


def test_precedence_security_over_user_and_mcp() -> None:
    env = {
        "ATHENA_CLIO_SECURITY_LEVEL": LEVEL_NONE,
        "ATHENA_CLIO_USER_LEVEL": LEVEL_COMPLETE,
    }
    ctx = LevelContext(mcp_suggestion=LEVEL_COMPLETE)
    assert resolve_level(ctx, env=env) == LEVEL_NONE


def test_project_caps_mcp_suggestion() -> None:
    env = {"ATHENA_CLIO_PROJECT_LEVEL": LEVEL_PARTIAL}
    ctx = LevelContext(mcp_suggestion=LEVEL_COMPLETE)
    assert resolve_level(ctx, env=env) == LEVEL_PARTIAL


def test_complete_without_protector_is_not_content_allowed() -> None:
    assert not complete_content_allowed(LEVEL_COMPLETE, protector_available=False)
    assert complete_content_allowed(LEVEL_COMPLETE, protector_available=True)


# ---------------------------------------------------------------------------
# Sanitizer adversarial
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("forbidden", sorted(FORBIDDEN_FIELD_NAMES))
def test_forbidden_fields_rejected_in_payload(forbidden: str) -> None:
    payload = build_technical_payload(_technical_event(), level=LEVEL_TECHNICAL)
    payload[forbidden] = "leak"
    with pytest.raises(ValueError):
        validate_payload(payload)


def test_redact_text_strips_secrets_and_urls() -> None:
    raw = "token=abc123 bearer deadbeef https://user:pass@host/path api_key=xyz"
    cleaned = redact_text(raw)
    assert "abc123" not in cleaned
    assert "deadbeef" not in cleaned
    assert "pass@" not in cleaned
    assert "xyz" not in cleaned


def test_serialize_event_enforces_byte_limit() -> None:
    payload = build_technical_payload(_technical_event(), level=LEVEL_TECHNICAL)
    payload["error_code"] = "x" * 20_000
    with pytest.raises(ValueError):
        serialize_event(payload)


def test_technical_identifier_fields_strip_secrets() -> None:
    event = _technical_event(
        provider="openai bearer sk-live-secret-token",
        error_code="token=abc123 TIMEOUT",
        tool="run_combo",
    )
    payload = build_technical_payload(event, level=LEVEL_TECHNICAL)
    assert "sk-live" not in payload.get("provider", "")
    assert "abc123" not in payload.get("error_code", "")
    assert payload["error_code"] == "TOKENREDACTEDTIMEOUT"
    assert "bearer" not in payload.get("provider", "").lower()


def test_normalize_identifier_removes_forbidden_charset() -> None:
    cleaned = normalize_identifier("run@combo! secret=xyz")
    assert "@" not in cleaned
    assert "!" not in cleaned
    assert "xyz" not in cleaned


def test_normalize_error_code_uppercases_and_strips() -> None:
    assert normalize_error_code("err_01") == "ERR_01"
    assert normalize_error_code("api_key=deadbeef") == "API_KEYREDACTED"


@pytest.mark.parametrize(
    "bad_timestamp",
    (
        "not-a-timestamp",
        "2026-08-29 12:00:00",
        "2026-08-29T12:00:00",
        "free-text persistence channel",
        "2026-08-29T12:00:00+0000",
    ),
)
def test_adversarial_timestamp_rejected(bad_timestamp: str) -> None:
    event = _technical_event(timestamp=bad_timestamp)
    with pytest.raises(ValueError, match="timestamp"):
        build_technical_payload(event, level=LEVEL_TECHNICAL)
    payload = build_technical_payload(_technical_event(), level=LEVEL_TECHNICAL)
    payload["timestamp"] = bad_timestamp
    with pytest.raises(ValueError, match="timestamp"):
        validate_payload(payload)
    producer = ClioProducer(level=LEVEL_TECHNICAL)
    assert producer.enqueue(payload) is False
    assert producer.counters.dropped_invalid == 1


def test_normalize_timestamp_accepts_utc_and_offset() -> None:
    assert normalize_timestamp("2026-08-29T12:00:00+00:00") == "2026-08-29T12:00:00+00:00"
    assert normalize_timestamp("2026-08-29T12:00:00Z") == "2026-08-29T12:00:00+00:00"
    assert normalize_timestamp("2026-08-29T08:00:00-04:00") == "2026-08-29T08:00:00-04:00"


# ---------------------------------------------------------------------------
# Producer / none bypass
# ---------------------------------------------------------------------------


def test_none_skips_queue_writer_and_storage(tmp_path: Path) -> None:
    producer = ClioProducer(level=LEVEL_NONE)
    payload = build_technical_payload(_technical_event(), level=LEVEL_TECHNICAL)
    assert producer.enqueue(payload) is False
    assert producer.queue is None
    assert producer.counters.none_bypass == 1
    store = ClioStore(tmp_path / "state")
    assert not store.state_dir.exists()


def test_queue_full_increments_counter_without_blocking() -> None:
    counters = MutableClioCounters()
    producer = ClioProducer(level=LEVEL_TECHNICAL, counters=counters, queue_capacity=2)
    payload = build_technical_payload(_technical_event(), level=LEVEL_TECHNICAL)
    assert producer.enqueue(payload) is True
    assert producer.enqueue(payload) is True
    assert producer.enqueue(payload) is False
    assert counters.dropped_queue_full == 1


def test_invalid_event_increments_counter() -> None:
    producer = ClioProducer(level=LEVEL_TECHNICAL)
    assert producer.enqueue({"bad": True}) is False
    assert producer.counters.dropped_invalid == 1


def test_enqueue_never_starts_writer_thread(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    started_during_enqueue = False
    original_start = threading.Thread.start

    def tracking_start(self: threading.Thread) -> None:
        nonlocal started_during_enqueue
        if threading.current_thread() is not threading.main_thread():
            return original_start(self)
        started_during_enqueue = True
        return original_start(self)

    monkeypatch.setattr(threading.Thread, "start", tracking_start)
    emitter = ClioEmitter(state_dir=tmp_path / "state")
    assert emitter._writer is not None
    assert emitter._writer.started
    started_during_enqueue = False
    emitter.emit_flow_started(
        task_handle="th-hot",
        execution_id="ex-hot",
        tool="run_combo",
    )
    assert started_during_enqueue is False


def test_none_level_never_starts_writer_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    start_calls: list[str] = []
    original_start = threading.Thread.start

    def tracking_start(self: threading.Thread) -> None:
        start_calls.append(self.name)
        return original_start(self)

    monkeypatch.setattr(threading.Thread, "start", tracking_start)
    emitter = ClioEmitter(
        state_dir=tmp_path / "state-none",
        env={"ATHENA_CLIO_LEVEL": LEVEL_NONE},
    )
    emitter.emit_flow_started(
        task_handle="th-none",
        execution_id="ex-none",
        tool="run_combo",
    )
    assert emitter._writer is None
    assert start_calls == []


# ---------------------------------------------------------------------------
# Persistence and retention
# ---------------------------------------------------------------------------


def test_writer_persists_and_survives_restart(tmp_path: Path) -> None:
    emitter = ClioEmitter(state_dir=tmp_path / "state")
    emitter.emit_flow_started(
        task_handle="th-persist",
        execution_id="ex-persist",
        tool="run_combo",
    )
    emitter.flush_for_tests()
    emitter.shutdown()
    db_path = tmp_path / "state" / "clio.sqlite3"
    assert db_path.exists()
    store = ClioStore(tmp_path / "state")
    assert store.count_events() >= 1
    assert "flow.task.started" in store.list_event_types()


def test_complete_without_protector_persists_no_plaintext(tmp_path: Path) -> None:
    emitter = ClioEmitter(
        state_dir=tmp_path / "state",
        env={"ATHENA_CLIO_LEVEL": LEVEL_COMPLETE},
        protector=None,
    )
    emitter.emit_flow_started(
        task_handle="th-complete-start",
        execution_id="ex-complete-start",
        tool="run_combo",
    )
    emitter.emit_flow_finished(
        task_handle="th-complete",
        execution_id="ex-complete",
        tool="run_combo",
        flow_payload={
            "validation_status": "pass",
            "delivery_status": "awaiting_human_review",
            "chronos_action": "CLOSED",
            "attempts_used": 1,
            "reason_codes": ["ALL_CHECKS_PASSED"],
        },
    )
    assert emitter.counters.dropped_complete_unavailable == 2
    emitter.flush_for_tests()
    emitter.shutdown()
    store = ClioStore(tmp_path / "state")
    connection = sqlite3.connect(store.db_path)
    rows = connection.execute("SELECT payload_json FROM clio_events").fetchall()
    connection.close()
    for (payload_json,) in rows:
        assert "protected_envelope" not in payload_json
        for forbidden in ("prompt", "command", "stdout", "stderr", "secret"):
            assert forbidden not in payload_json.lower()


def test_complete_with_protector_persists_envelope(tmp_path: Path) -> None:
    emitter = ClioEmitter(
        state_dir=tmp_path / "state",
        env={"ATHENA_CLIO_LEVEL": LEVEL_COMPLETE},
        protector=_StubProtector(),
    )
    emitter.emit_flow_finished(
        task_handle="th-env",
        execution_id="ex-env",
        tool="run_combo",
        flow_payload={
            "validation_status": "pass",
            "delivery_status": "awaiting_human_review",
            "chronos_action": "CLOSED",
            "attempts_used": 1,
            "reason_codes": [],
        },
    )
    emitter.flush_for_tests()
    emitter.shutdown()
    store = ClioStore(tmp_path / "state")
    connection = sqlite3.connect(store.db_path)
    row = connection.execute(
        "SELECT payload_json FROM clio_events WHERE event_type = 'flow.task.finished'"
    ).fetchone()
    connection.close()
    assert row is not None
    payload = json.loads(row[0])
    assert "protected_envelope" in payload


def test_level_change_emits_technical_event(tmp_path: Path) -> None:
    emitter = ClioEmitter(
        state_dir=tmp_path / "state",
        env={"ATHENA_CLIO_LEVEL": LEVEL_TECHNICAL},
    )
    emitter.refresh_level(LevelContext(global_level=LEVEL_PARTIAL))
    emitter.flush_for_tests()
    emitter.shutdown()
    store = ClioStore(tmp_path / "state")
    assert "clio.level_changed" in store.list_event_types()


def test_level_transition_none_to_active_is_deterministic(tmp_path: Path) -> None:
    emitter = ClioEmitter(
        state_dir=tmp_path / "state",
        env={"ATHENA_CLIO_LEVEL": LEVEL_NONE},
    )
    assert emitter._writer is None
    emitter.refresh_level(LevelContext(global_level=LEVEL_TECHNICAL))
    assert emitter._writer is not None
    assert emitter._writer.started
    emitter.emit_flow_started(
        task_handle="th-reactivate",
        execution_id="ex-reactivate",
        tool="run_combo",
    )
    emitter.flush_for_tests()
    emitter.shutdown()
    store = ClioStore(tmp_path / "state")
    assert store.count_events() >= 2
    assert "flow.task.started" in store.list_event_types()
    assert "clio.level_changed" in store.list_event_types()


def test_level_transition_active_to_none_stops_writer(tmp_path: Path) -> None:
    emitter = ClioEmitter(
        state_dir=tmp_path / "state",
        env={"ATHENA_CLIO_LEVEL": LEVEL_TECHNICAL},
    )
    writer = emitter._writer
    assert writer is not None
    emitter.refresh_level(LevelContext(global_level=LEVEL_NONE))
    assert emitter._writer is None
    assert not writer.started
    emitter.emit_flow_started(
        task_handle="th-after-none",
        execution_id="ex-after-none",
        tool="run_combo",
    )
    assert emitter.counters.none_bypass >= 1


def test_writer_survives_sqlite_failure_and_recovers(tmp_path: Path) -> None:
    store = ClioStore(tmp_path / "state")
    counters = MutableClioCounters()
    producer = ClioProducer(level=LEVEL_TECHNICAL, counters=counters)
    writer = ClioWriter(producer, store, counters=counters)
    writer.start()
    calls = {"count": 0}
    original_insert = store.insert_batch

    def flaky_insert(rows: object) -> int:
        calls["count"] += 1
        if calls["count"] == 1:
            raise sqlite3.OperationalError("injected failure")
        return original_insert(rows)  # type: ignore[arg-type]

    store.insert_batch = flaky_insert  # type: ignore[method-assign]
    payload = build_technical_payload(_technical_event(), level=LEVEL_TECHNICAL)
    producer.enqueue(payload)
    writer.flush_sync()
    time.sleep(0.2)
    producer.enqueue(
        build_technical_payload(
            _technical_event(task_handle="th-recover", execution_id="ex-recover"),
            level=LEVEL_TECHNICAL,
        )
    )
    writer.flush_sync()
    time.sleep(0.2)
    assert counters.writer_failures >= 1
    assert store.count_events() >= 1
    writer.shutdown()


def test_shutdown_bounded_despite_slow_store(tmp_path: Path) -> None:
    store = ClioStore(tmp_path / "state")
    counters = MutableClioCounters()
    producer = ClioProducer(level=LEVEL_TECHNICAL, counters=counters)
    writer = ClioWriter(producer, store, counters=counters)
    writer.start()
    original_insert = store.insert_batch
    blocked = threading.Event()
    release = threading.Event()

    def blocking_insert(rows: object) -> int:
        blocked.set()
        release.wait(timeout=5.0)
        return original_insert(rows)  # type: ignore[arg-type]

    store.insert_batch = blocking_insert  # type: ignore[method-assign]
    payload = build_technical_payload(_technical_event(), level=LEVEL_TECHNICAL)
    producer.enqueue(payload)
    assert blocked.wait(timeout=2.0)
    failures_before = counters.writer_failures
    start = time.monotonic()
    joined = writer.shutdown(timeout_s=0.3)
    elapsed = time.monotonic() - start
    assert elapsed < SHUTDOWN_TIMEOUT_S + 0.5
    assert joined is False
    assert writer.thread_alive()
    assert counters.writer_failures == failures_before + 1
    release.set()
    assert writer.join_thread(timeout_s=2.0)
    assert not writer.started
    assert not writer.thread_alive()
    assert store.count_events() >= 1


def test_reactivation_uses_exclusive_producer_generation(tmp_path: Path) -> None:
    store = ClioStore(tmp_path / "state")
    counters = MutableClioCounters()
    producer_v1 = ClioProducer(level=LEVEL_TECHNICAL, counters=counters)
    writer_v1 = ClioWriter(producer_v1, store, counters=counters)
    writer_v1.start()
    original_insert = store.insert_batch
    blocked = threading.Event()
    release = threading.Event()

    def blocking_insert(rows: object) -> int:
        blocked.set()
        release.wait(timeout=5.0)
        return original_insert(rows)  # type: ignore[arg-type]

    store.insert_batch = blocking_insert  # type: ignore[method-assign]
    producer_v1.enqueue(build_technical_payload(_technical_event(), level=LEVEL_TECHNICAL))
    assert blocked.wait(timeout=2.0)
    assert writer_v1.shutdown(timeout_s=0.2) is False
    assert writer_v1.thread_alive()
    queue_v1 = producer_v1.queue
    assert queue_v1 is not None

    producer_v2 = ClioProducer(level=LEVEL_TECHNICAL, counters=counters)
    writer_v2 = ClioWriter(producer_v2, store, counters=counters)
    writer_v2.start()
    assert producer_v2.queue is not queue_v1
    producer_v2.enqueue(
        build_technical_payload(
            _technical_event(task_handle="th-v2", execution_id="ex-v2"),
            level=LEVEL_TECHNICAL,
        )
    )
    release.set()
    assert writer_v1.join_thread(timeout_s=2.0)
    writer_v2.flush_sync()
    writer_v2.shutdown()
    assert store.count_events() >= 1


def test_refresh_level_stops_writer_before_retiring_producer(tmp_path: Path) -> None:
    emitter = ClioEmitter(
        state_dir=tmp_path / "state",
        env={"ATHENA_CLIO_LEVEL": LEVEL_TECHNICAL},
    )
    producer = emitter._producer
    assert producer is not None
    emitter.emit_flow_started(
        task_handle="th-queued",
        execution_id="ex-queued",
        tool="run_combo",
    )
    queued_before = producer.counters.enqueued
    assert queued_before >= 1
    emitter.refresh_level(LevelContext(global_level=LEVEL_PARTIAL))
    emitter.flush_for_tests()
    emitter.shutdown()
    store = ClioStore(tmp_path / "state")
    assert "flow.task.started" in store.list_event_types()
    assert "clio.level_changed" in store.list_event_types()


def test_writer_started_false_after_thread_exit(tmp_path: Path) -> None:
    store = ClioStore(tmp_path / "state")
    producer = ClioProducer(level=LEVEL_TECHNICAL)
    writer = ClioWriter(producer, store)
    writer.start()
    assert writer.started
    assert writer.shutdown()
    assert not writer.thread_alive()
    assert not writer.started


def test_flush_for_tests_waits_for_persistence_not_empty_queue(tmp_path: Path) -> None:
    store = ClioStore(tmp_path / "state")
    counters = MutableClioCounters()
    producer = ClioProducer(level=LEVEL_TECHNICAL, counters=counters)
    writer = ClioWriter(producer, store, counters=counters)
    writer.start()
    original_insert = store.insert_batch
    blocked = threading.Event()
    release = threading.Event()
    inserted = threading.Event()

    def blocking_insert(rows: object) -> int:
        blocked.set()
        release.wait(timeout=5.0)
        result = original_insert(rows)  # type: ignore[arg-type]
        inserted.set()
        return result

    store.insert_batch = blocking_insert  # type: ignore[method-assign]
    producer.enqueue(build_technical_payload(_technical_event(), level=LEVEL_TECHNICAL))
    assert blocked.wait(timeout=2.0)
    flush_thread = threading.Thread(target=writer.flush_sync, daemon=True)
    flush_thread.start()
    time.sleep(0.1)
    assert flush_thread.is_alive()
    release.set()
    flush_thread.join(timeout=2.0)
    assert not flush_thread.is_alive()
    assert inserted.is_set()
    assert store.count_events() >= 1
    writer.shutdown()


def test_level_transition_timed_out_reactivation_uses_new_queue(tmp_path: Path) -> None:
    emitter = ClioEmitter(
        state_dir=tmp_path / "state",
        env={"ATHENA_CLIO_LEVEL": LEVEL_TECHNICAL},
    )
    writer_v1 = emitter._writer
    producer_v1 = emitter._producer
    assert writer_v1 is not None and producer_v1 is not None
    original_insert = emitter.store.insert_batch
    blocked = threading.Event()
    release = threading.Event()

    def blocking_insert(rows: object) -> int:
        blocked.set()
        release.wait(timeout=5.0)
        return original_insert(rows)  # type: ignore[arg-type]

    emitter.store.insert_batch = blocking_insert  # type: ignore[method-assign]
    emitter.emit_flow_started(
        task_handle="th-timeout",
        execution_id="ex-timeout",
        tool="run_combo",
    )
    assert blocked.wait(timeout=2.0)
    emitter.refresh_level(LevelContext(global_level=LEVEL_NONE))
    assert emitter._writer is None
    assert writer_v1.thread_alive()
    queue_v1 = producer_v1.queue

    emitter.refresh_level(LevelContext(global_level=LEVEL_TECHNICAL))
    producer_v2 = emitter._producer
    writer_v2 = emitter._writer
    assert producer_v2 is not None and writer_v2 is not None
    assert producer_v2 is not producer_v1
    assert producer_v2.queue is not queue_v1
    release.set()
    assert writer_v1.join_thread(timeout_s=2.0)
    emitter.emit_flow_started(
        task_handle="th-after",
        execution_id="ex-after",
        tool="run_combo",
    )
    emitter.flush_for_tests()
    emitter.shutdown()
    store = ClioStore(tmp_path / "state")
    assert store.count_events() >= 2


def test_state_dir_permissions(tmp_path: Path) -> None:
    store = ClioStore(tmp_path / "state")
    store.insert_batch(
        [
            (
                "clio.event.v1",
                "flow.task.started",
                LEVEL_TECHNICAL,
                "2026-08-29T12:00:00+00:00",
                "{}",
            )
        ]
    )
    dir_mode = oct(tmp_path.joinpath("state").stat().st_mode & 0o777)
    file_mode = oct(store.db_path.stat().st_mode & 0o777)
    assert dir_mode == oct(0o700)
    assert file_mode == oct(0o600)


# ---------------------------------------------------------------------------
# Emitter integration
# ---------------------------------------------------------------------------


def test_build_clio_emitter_default_technical() -> None:
    emitter = build_clio_emitter()
    assert emitter is not None
    assert emitter.level == LEVEL_TECHNICAL


def test_build_clio_emitter_none_returns_none() -> None:
    emitter = build_clio_emitter(env={"ATHENA_CLIO_LEVEL": LEVEL_NONE})
    assert emitter is None


def test_emitter_shutdown_is_bounded(tmp_path: Path) -> None:
    emitter = ClioEmitter(state_dir=tmp_path / "state")
    for i in range(10):
        emitter.emit_flow_started(
            task_handle=f"th-{i}",
            execution_id=f"ex-{i}",
            tool="run_combo",
        )
    emitter.shutdown()


def test_repeated_blocked_transitions_bound_live_generations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("athena.clio.emitter.SHUTDOWN_TIMEOUT_S", 0.05)
    emitter = ClioEmitter(
        state_dir=tmp_path / "state",
        env={"ATHENA_CLIO_LEVEL": LEVEL_TECHNICAL},
    )
    original_insert = emitter.store.insert_batch
    blocked = threading.Event()
    release = threading.Event()
    queue_ids: set[int] = set()

    def assert_live_queues_exclusive() -> None:
        live_ids: list[int] = []
        if emitter._producer is not None and emitter._producer.queue is not None:
            live_ids.append(id(emitter._producer.queue))
        for _writer, producer in emitter._retired_generations:
            if producer.queue is not None:
                live_ids.append(id(producer.queue))
        assert len(live_ids) == len(set(live_ids))
        queue_ids.update(live_ids)

    def blocking_insert(rows: object) -> int:
        blocked.set()
        release.wait(timeout=10.0)
        return original_insert(rows)  # type: ignore[arg-type]

    emitter.store.insert_batch = blocking_insert  # type: ignore[method-assign]
    emitter.emit_flow_started(
        task_handle="th-block-0",
        execution_id="ex-block-0",
        tool="run_combo",
    )
    assert blocked.wait(timeout=2.0)
    assert_live_queues_exclusive()

    cycles = MAX_RETIRED_GENERATIONS + 3
    for index in range(cycles):
        emitter.refresh_level(LevelContext(global_level=LEVEL_NONE))
        emitter.refresh_level(LevelContext(global_level=LEVEL_TECHNICAL))
        start = time.monotonic()
        emitter.emit_flow_started(
            task_handle=f"th-block-{index + 1}",
            execution_id=f"ex-block-{index + 1}",
            tool="run_combo",
        )
        assert time.monotonic() - start < 0.05
        assert_live_queues_exclusive()

    emitter._prune_retired_generations()
    assert len(emitter._retired_generations) <= MAX_RETIRED_GENERATIONS
    assert emitter.counters.dropped_writer_capacity >= 1
    assert len(queue_ids) <= MAX_RETIRED_GENERATIONS + 1

    release.set()
    for writer, _producer in list(emitter._retired_generations):
        assert writer.join_thread(timeout_s=2.0)
        assert not writer.thread_alive()
    emitter.shutdown(timeout_s=2.0)
    if emitter._writer is not None:
        assert not emitter._writer.thread_alive()


def test_emitter_shutdown_total_budget_active_and_retired_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("athena.clio.emitter.SHUTDOWN_TIMEOUT_S", 0.05)
    emitter = ClioEmitter(state_dir=tmp_path / "state")
    original_insert = emitter.store.insert_batch
    blocked = threading.Event()
    release = threading.Event()

    def blocking_insert(rows: object) -> int:
        blocked.set()
        release.wait(timeout=10.0)
        return original_insert(rows)  # type: ignore[arg-type]

    emitter.store.insert_batch = blocking_insert  # type: ignore[method-assign]
    emitter.emit_flow_started(
        task_handle="th-budget-0",
        execution_id="ex-budget-0",
        tool="run_combo",
    )
    assert blocked.wait(timeout=2.0)
    writer_v1 = emitter._writer
    producer_v1 = emitter._producer
    assert writer_v1 is not None and producer_v1 is not None
    queue_v1 = producer_v1.queue

    emitter.refresh_level(LevelContext(global_level=LEVEL_NONE))
    assert writer_v1.thread_alive()
    assert len(emitter._retired_generations) <= MAX_RETIRED_GENERATIONS

    emitter.refresh_level(LevelContext(global_level=LEVEL_TECHNICAL))
    writer_v2 = emitter._writer
    producer_v2 = emitter._producer
    assert writer_v2 is not None and producer_v2 is not None
    assert producer_v2.queue is not queue_v1
    emitter.emit_flow_started(
        task_handle="th-budget-1",
        execution_id="ex-budget-1",
        tool="run_combo",
    )

    shutdown_budget_s = 0.3
    start = time.monotonic()
    emitter.shutdown(timeout_s=shutdown_budget_s)
    elapsed = time.monotonic() - start
    assert elapsed < shutdown_budget_s + 0.15
    assert elapsed < SHUTDOWN_TIMEOUT_S * 2
    assert writer_v1.thread_alive()
    assert writer_v2.thread_alive()

    release.set()
    assert writer_v1.join_thread(timeout_s=2.0)
    if writer_v2.thread_alive():
        assert writer_v2.join_thread(timeout_s=2.0)
    assert not writer_v1.thread_alive()
    assert not writer_v2.thread_alive()


# ---------------------------------------------------------------------------
# Benchmark 30/3
# ---------------------------------------------------------------------------


def test_clio_enqueue_benchmark_30_3_guardrail(tmp_path: Path) -> None:
    config = clio_benchmark.BenchmarkConfig(
        samples=30,
        warmups=3,
        guardrail=True,
        enqueue_ceiling_ms=1.0,
        none_ceiling_ms=0.05,
    )
    report = clio_benchmark.run_benchmark(config, state_dir=tmp_path / "bench")
    guard = report["guardrail"]
    assert guard["enqueue_pass"] is True
    assert guard["none_pass"] is True
    assert report["none"]["none_bypass"] == 33.0


# ---------------------------------------------------------------------------
# JSON-RPC smoke — technical persistence and none zero storage
# ---------------------------------------------------------------------------


def _run_athena_requests(
    state_dir: Path,
    requests: tuple[dict[str, Any], ...],
    *,
    extra_env: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    env = dict(os.environ)
    env["ATHENA_STATE_DIR"] = str(state_dir)
    if extra_env:
        env.update(extra_env)
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


def test_jsonrpc_smoke_clio_technical_persistence(tmp_path: Path) -> None:
    from tests.route0_support import routing_arguments, write_route_config

    state_dir = tmp_path / "state-tech"
    config_dir = write_route_config(tmp_path / "config", providers=("echo",))
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_text("ok", encoding="utf-8")

    submit_resp = _run_athena_requests(
        state_dir,
        (
            {
                "jsonrpc": "2.0",
                "id": "s1",
                "method": "tools/call",
                "params": {
                    "name": "submit_task",
                    "arguments": {
                        "idempotency_key": "clio-smoke-tech",
                        "task": {"task_type": "demo.task", "input": "clio"},
                    },
                },
            },
        ),
        extra_env={"ATHENA_CLIO_LEVEL": LEVEL_TECHNICAL},
    )
    handle = json.loads(submit_resp[0]["result"]["content"][0]["text"])["task_handle"]

    _run_athena_requests(
        state_dir,
        (
            {
                "jsonrpc": "2.0",
                "id": "r1",
                "method": "tools/call",
                "params": {
                    "name": "run_combo",
                    "arguments": {
                        **routing_arguments(),
                        "attempts": [
                            {
                                "provider": "echo",
                                "command": ["echo", "clio-smoke"],
                                "cwd": str(tmp_path),
                            }
                        ],
                        "task_handle": handle,
                        "verification": {"files": [str(sentinel)]},
                    },
                },
            },
        ),
        extra_env={"ATHENA_CONFIG_DIR": str(config_dir), "ATHENA_CLIO_LEVEL": LEVEL_TECHNICAL},
    )

    clio_db = state_dir / "clio.sqlite3"
    assert clio_db.exists()
    connection = sqlite3.connect(clio_db)
    count = connection.execute("SELECT COUNT(*) FROM clio_events").fetchone()[0]
    types = {
        row[0]
        for row in connection.execute(
            "SELECT DISTINCT event_type FROM clio_events"
        ).fetchall()
    }
    connection.close()
    assert count >= 2
    assert "flow.task.started" in types
    assert "flow.task.finished" in types


def test_jsonrpc_smoke_clio_none_zero_storage(tmp_path: Path) -> None:
    state_dir = tmp_path / "state-none"
    _run_athena_requests(
        state_dir,
        (
            {
                "jsonrpc": "2.0",
                "id": "l1",
                "method": "tools/list",
                "params": {},
            },
        ),
        extra_env={"ATHENA_CLIO_LEVEL": LEVEL_NONE},
    )
    assert not (state_dir / "clio.sqlite3").exists()
    if state_dir.exists():
        assert "clio.sqlite3" not in {p.name for p in state_dir.iterdir()}


def test_producer_never_raises_on_full_queue() -> None:
    producer = ClioProducer(level=LEVEL_TECHNICAL, queue_capacity=1)
    payload = build_technical_payload(_technical_event(), level=LEVEL_TECHNICAL)
    producer.enqueue(payload)
    producer.enqueue(payload)  # must not raise
    assert producer.counters.dropped_queue_full == 1
