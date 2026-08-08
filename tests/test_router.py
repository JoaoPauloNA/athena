"""Testes do router (failover de combos)."""
import pytest

from athena import router
from athena.bridge import RunResult
from athena.combos import Combo, ComboStep, FailoverPolicy


def _ok(provider):
    return RunResult(provider=provider, command=[], output="ok", exit_code=0)


def _fail(provider):
    return RunResult(provider=provider, command=[], output="", exit_code=1, error="boom")


def _combo(retries=1):
    return Combo(
        id="t",
        name="t",
        chain=[ComboStep(provider_id="p1"), ComboStep(provider_id="p2")],
        failover_policy=FailoverPolicy(max_retries_per_provider=retries),
    )


def test_failover_to_second_provider(monkeypatch):
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    calls = []

    def fake_ask(provider_id, prompt, **kw):
        calls.append(provider_id)
        return _fail(provider_id) if provider_id == "p1" else _ok(provider_id)

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    result = router.run_combo("t", "prompt")
    assert result.exit_code == 0
    assert calls == ["p1", "p2"]


def test_all_providers_failed(monkeypatch):
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    monkeypatch.setattr(router, "ask_provider", lambda p, pr, **kw: _fail(p))
    with pytest.raises(router.AllProvidersFailed):
        router.run_combo("t", "prompt")


def test_combo_not_found(monkeypatch):
    monkeypatch.setattr(router, "get_combo", lambda cid: None)
    with pytest.raises(ValueError):
        router.run_combo("inexistente", "prompt")
