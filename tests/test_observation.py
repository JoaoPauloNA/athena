"""Testes da observação sombra (MO-1/MO-2): sanitização, opt-in, sem autoridade."""

from __future__ import annotations

import pytest

from athena.execution import ExecutionDeadlines, ExecutionRecord, ExecutionState
from athena.observation import (
    ALLOWED_EVENT_FIELDS,
    ENV_FLAG,
    FORBIDDEN_FIELD_NAMES,
    ShadowEmitter,
    ShadowExecutionEvent,
    shadow_enabled,
    validate_event_payload,
)


class RecordingObserver:
    def __init__(self) -> None:
        self.events: list[ShadowExecutionEvent] = []

    def observe(self, event: ShadowExecutionEvent) -> None:
        self.events.append(event)


class BrokenObserver:
    def observe(self, event: ShadowExecutionEvent) -> None:
        raise RuntimeError("observer quebrado não pode derrubar execução")


def _record() -> ExecutionRecord:
    return ExecutionRecord(
        "probe-provider",
        profile="text_generation",
        execution_id="exec-1",
        attempt_id="att-1",
        deadlines=ExecutionDeadlines(absolute_timeout_s=5),
    )


# ---------------------------------------------------------------- contrato


def test_event_payload_respeita_allowlist():
    event = ShadowExecutionEvent(
        execution_id="e", attempt_id="a", provider="p",
        state="RUNNING", progress_counter=1,
        duration_s=0.5, expired_deadline=None, cancelled_by_client=False,
    )
    payload = event.to_dict()
    assert set(payload) <= ALLOWED_EVENT_FIELDS
    assert not (FORBIDDEN_FIELD_NAMES & set(payload))


@pytest.mark.parametrize("campo", ["prompt", "stdout", "command", "env", "text"])
def test_campos_proibidos_sao_rejeitados(campo):
    with pytest.raises(ValueError):
        validate_event_payload({campo: "qualquer-coisa"})


def test_campo_fora_da_allowlist_é_rejeitado():
    payload = {k: "x" for k in ALLOWED_EVENT_FIELDS}
    payload["secret_extra"] = "não permitido"
    with pytest.raises(ValueError):
        validate_event_payload(payload)


def test_evento_valida_tipos():
    base = {
        "execution_id": "e",
        "attempt_id": "a",
        "provider": "p",
        "state": "RUNNING",
        "progress_counter": 0,
        "duration_s": None,
        "expired_deadline": None,
        "cancelled_by_client": False,
    }
    with pytest.raises(ValueError):
        ShadowExecutionEvent(**{**base, "execution_id": ""})
    with pytest.raises(ValueError):
        ShadowExecutionEvent(**{**base, "progress_counter": -1})
    with pytest.raises(TypeError):
        ShadowExecutionEvent(**{**base, "cancelled_by_client": "sim"})
    with pytest.raises(TypeError):
        ShadowExecutionEvent(**{**base, "progress_counter": "muito"})


# ---------------------------------------------------------------- emitter


def test_emitter_inativo_por_padrao_sem_env(monkeypatch):
    monkeypatch.delenv(ENV_FLAG, raising=False)
    assert not shadow_enabled({})
    observer = RecordingObserver()
    emitter = ShadowEmitter(observer, enabled=None)
    assert not emitter.active
    emitter.emit_transition(_record(), ExecutionState.RUNNING)
    assert observer.events == []


def test_emitter_ativo_com_env(monkeypatch):
    monkeypatch.setenv(ENV_FLAG, "1")
    assert shadow_enabled()
    observer = RecordingObserver()
    emitter = ShadowEmitter(observer)
    record = _record()
    record.transition(ExecutionState.STARTING)
    record.transition(ExecutionState.RUNNING)
    record.record_progress()
    emitter.emit_transition(record, ExecutionState.RUNNING)
    assert len(observer.events) == 1
    ev = observer.events[0]
    assert ev.execution_id == "exec-1"
    assert ev.attempt_id == "att-1"
    assert ev.provider == "probe-provider"
    assert ev.state == "running"
    assert isinstance(ev.progress_counter, int)
    # nenhum campo proibido vazou
    payload = ev.to_dict()
    assert not (FORBIDDEN_FIELD_NAMES & set(payload))


def test_observer_quebrado_nao_propaga():
    emitter = ShadowEmitter(BrokenObserver(), enabled=True)
    assert emitter.active
    emitter.emit_transition(_record(), ExecutionState.COMPLETED)
    assert emitter.suppressed_errors == 1  # engolido e contado


def test_flag_desligada_desativa_mesmo_com_observer():
    emitter = ShadowEmitter(RecordingObserver(), enabled=False)
    assert not emitter.active
