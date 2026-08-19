"""Testes determinísticos da escalada ao orquestrador após falhas verificáveis."""

from __future__ import annotations

import json
import os

import pytest

import athena.orchestrator_escalation as escalation
from athena import router, workspace_lease
from athena.bridge import RunResult
from athena.combos import Combo, ComboStep, FailoverPolicy


@pytest.fixture(autouse=True)
def _clean_escalation_and_lease_state():
    escalation._reset_for_tests()
    workspace_lease._reset_for_tests()
    old_distinct = os.environ.get("ATHENA_ESCALATION_DISTINCT_THRESHOLD")
    old_max = os.environ.get("ATHENA_ESCALATION_MAX_ATTEMPTS")
    old_cb = os.environ.get("ATHENA_ESCALATION_CIRCUIT_BREAKER")
    os.environ["ATHENA_ESCALATION_DISTINCT_THRESHOLD"] = "3"
    os.environ["ATHENA_ESCALATION_MAX_ATTEMPTS"] = "6"
    os.environ["ATHENA_ESCALATION_CIRCUIT_BREAKER"] = "2"
    escalation.DISTINCT_PROVIDER_THRESHOLD = 3
    escalation.MAX_FAILURE_ATTEMPTS = 6
    escalation.CIRCUIT_BREAKER_SIGNATURE_THRESHOLD = 2
    yield
    escalation._reset_for_tests()
    workspace_lease._reset_for_tests()
    if old_distinct is None:
        os.environ.pop("ATHENA_ESCALATION_DISTINCT_THRESHOLD", None)
    else:
        os.environ["ATHENA_ESCALATION_DISTINCT_THRESHOLD"] = old_distinct
    if old_max is None:
        os.environ.pop("ATHENA_ESCALATION_MAX_ATTEMPTS", None)
    else:
        os.environ["ATHENA_ESCALATION_MAX_ATTEMPTS"] = old_max
    if old_cb is None:
        os.environ.pop("ATHENA_ESCALATION_CIRCUIT_BREAKER", None)
    else:
        os.environ["ATHENA_ESCALATION_CIRCUIT_BREAKER"] = old_cb


def _fail(provider: str) -> RunResult:
    return RunResult(provider=provider, command=[], output="", exit_code=1, error="boom")


def _combo_three_providers() -> Combo:
    return Combo(
        id="escalation-test",
        name="escalation-test",
        chain=[
            ComboStep(provider_id="p1"),
            ComboStep(provider_id="p2"),
            ComboStep(provider_id="p3"),
        ],
        failover_policy=FailoverPolicy(max_retries_per_provider=1),
    )


def test_three_distinct_provider_failures_trigger_escalation(monkeypatch, tmp_path):
    monkeypatch.setattr(router, "get_combo", lambda _cid: _combo_three_providers())
    monkeypatch.setattr(router, "ask_provider", lambda p, pr, **kw: _fail(p))
    monkeypatch.setenv("ATHENA_LOGS_DIR", str(tmp_path / "logs"))

    with pytest.raises(router.OrchestratorEscalationRequired) as exc_info:
        router.run_combo("escalation-test", "prompt", execution_id="exec-esc-1")

    package = exc_info.value.package
    assert package["event_type"] == "orchestrator_escalation_required"
    assert package["status"] == "awaiting_orchestrator"
    assert package["execution_id"] == "exec-esc-1"
    assert package["episode_id"] == "exec-esc-1"
    assert package["distinct_providers_failed"] == 3
    assert package["attempted_chain"] == ["p1", "p2", "p3"]
    assert len(package["consecutive_failures"]) == 3
    assert package["continuation_hint"]["no_autonomous_loop"] is True

    events = list((tmp_path / "logs").glob("**/events.jsonl"))
    assert events
    lines = events[0].read_text(encoding="utf-8").splitlines()
    escalation_events = [
        json.loads(line)
        for line in lines
        if json.loads(line).get("event_type") == "orchestrator_escalation_required"
    ]
    assert len(escalation_events) == 1
    assert escalation_events[0]["execution_id"] == "exec-esc-1"


def test_two_provider_failures_still_raise_all_providers_failed(monkeypatch):
    combo = Combo(
        id="two",
        name="two",
        chain=[ComboStep(provider_id="p1"), ComboStep(provider_id="p2")],
        failover_policy=FailoverPolicy(max_retries_per_provider=1),
    )
    monkeypatch.setattr(router, "get_combo", lambda _cid: combo)
    monkeypatch.setattr(router, "ask_provider", lambda p, pr, **kw: _fail(p))

    with pytest.raises(router.AllProvidersFailed):
        router.run_combo("two", "prompt", execution_id="exec-two")


def test_orchestrator_escalation_is_subclass_of_all_providers_failed(monkeypatch):
    monkeypatch.setattr(router, "get_combo", lambda _cid: _combo_three_providers())
    monkeypatch.setattr(router, "ask_provider", lambda p, pr, **kw: _fail(p))

    with pytest.raises(router.AllProvidersFailed):
        router.run_combo("escalation-test", "prompt", execution_id="exec-subclass")


def test_circuit_breaker_trips_on_repeated_signature(monkeypatch):
    combo = Combo(
        id="cb",
        name="cb",
        chain=[ComboStep(provider_id="p1")],
        failover_policy=FailoverPolicy(max_retries_per_provider=3),
    )
    monkeypatch.setattr(router, "get_combo", lambda _cid: combo)
    monkeypatch.setattr(router, "ask_provider", lambda p, pr, **kw: _fail(p))

    with pytest.raises(router.OrchestratorEscalationRequired) as exc_info:
        router.run_combo("cb", "prompt", execution_id="exec-cb")

    assert exc_info.value.package["escalation_reason"] == "circuit_breaker_signature"
    assert exc_info.value.package["circuit_breaker_tripped"] is True


def test_orchestrator_continuation_resets_tracker_and_overrides_chain(monkeypatch):
    calls: list[str] = []

    def fake_ask(provider_id, prompt, **kwargs):
        calls.append(provider_id)
        if provider_id == "p9":
            return RunResult(provider=provider_id, command=[], output="ok", exit_code=0)
        return _fail(provider_id)

    monkeypatch.setattr(router, "get_combo", lambda _cid: _combo_three_providers())
    monkeypatch.setattr(router, "ask_provider", fake_ask)

    with pytest.raises(router.OrchestratorEscalationRequired):
        router.run_combo("escalation-test", "prompt", execution_id="exec-cont")

    result = router.run_combo(
        "escalation-test",
        "prompt",
        execution_id="exec-cont",
        orchestrator_continuation={
            "action": "retry",
            "reset_failure_tracker": True,
            "chain_provider_ids": ["p9"],
        },
    )
    assert result.exit_code == 0
    assert calls[-1] == "p9"


def test_classify_empty_output_failure():
    result = RunResult(provider="p1", command=[], output="   ", exit_code=0)
    failure = escalation.classify_verifiable_failure(
        provider_id="p1",
        result=result,
        execution_id="exec-empty",
    )
    assert failure is not None
    assert failure.kind == "empty_output"


def test_apply_orchestrator_continuation_rejects_invalid_action():
    with pytest.raises(ValueError):
        escalation.apply_orchestrator_continuation("exec-x", {"action": "autopilot"})
