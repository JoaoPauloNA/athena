"""Testes do verify no run_combo e da confiabilidade no recommend."""
import pytest

from athena import providers, router
from athena import recommend as rec
from athena.bridge import RunResult
from athena.combos import Combo, ComboStep, FailoverPolicy
from athena.providers import ProviderSpec
from athena.verifier import Verdict


def _ok(provider):
    return RunResult(provider=provider, command=[], output="relatório ok", exit_code=0)


def _combo():
    return Combo(
        id="t",
        name="t",
        chain=[ComboStep(provider_id="p1"), ComboStep(provider_id="p2")],
        failover_policy=FailoverPolicy(max_retries_per_provider=1),
    )


def _patch_verify(monkeypatch, verdicts):
    """verify_report falso: devolve Verdict por provider; grava chamadas de record."""
    recorded = []
    monkeypatch.setattr(
        "athena.verifier.verify_report",
        lambda task, out, **kwargs: verdicts[kwargs.get("executor_provider", "")](
            kwargs.get("execution_id"),
            kwargs.get("attempt_id"),
        ),
    )
    monkeypatch.setattr(
        "athena.reliability.record_verdict",
        lambda pid, v, *, task_excerpt="", project="": recorded.append((pid, v.verdadeiro)),
    )
    return recorded


def test_run_combo_verify_false_triggers_failover(monkeypatch):
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    monkeypatch.setattr(router, "ask_provider", lambda p, pr, **kw: _ok(p))
    recorded = _patch_verify(monkeypatch, {
        "p1": lambda execution_id, attempt_id: Verdict(
            verdadeiro=False,
            motivos=["exit 1"],
            verificador="deterministic",
            execution={
                "execution_id": execution_id,
                "attempt_id": attempt_id,
                "state": "COMPLETED",
                "process_created": False,
                "direct_process_terminated_confirmed": True,
                "process_tree_terminated_confirmed": True,
                "client_abandoned": False,
            },
        ),
        "p2": lambda execution_id, attempt_id: Verdict(
            verdadeiro=True,
            verificador="deterministic",
            execution={
                "execution_id": execution_id,
                "attempt_id": attempt_id,
                "state": "COMPLETED",
                "process_created": False,
                "direct_process_terminated_confirmed": True,
                "process_tree_terminated_confirmed": True,
                "client_abandoned": False,
            },
        ),
    })
    calls = []
    orig_ask = router.ask_provider
    monkeypatch.setattr(router, "ask_provider", lambda p, pr, **kw: (calls.append(p), orig_ask(p, pr, **kw))[1])

    result = router.run_combo("t", "prompt", verify=True)
    assert result.provider == "p2"
    assert result.verdict["verdadeiro"] is True
    assert calls == ["p1", "p2"]  # p1 rejeitado pelo verificador
    assert ("p1", False) in recorded and ("p2", True) in recorded


def test_run_combo_verify_all_false_raises(monkeypatch):
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    monkeypatch.setattr(router, "ask_provider", lambda p, pr, **kw: _ok(p))
    _patch_verify(monkeypatch, {
        "p1": lambda execution_id, attempt_id: Verdict(
            verdadeiro=False,
            motivos=["mentira"],
            execution={
                "execution_id": execution_id,
                "attempt_id": attempt_id,
                "state": "COMPLETED",
                "process_created": False,
                "direct_process_terminated_confirmed": True,
                "process_tree_terminated_confirmed": True,
                "client_abandoned": False,
            },
        ),
        "p2": lambda execution_id, attempt_id: Verdict(
            verdadeiro=False,
            motivos=["mentira"],
            execution={
                "execution_id": execution_id,
                "attempt_id": attempt_id,
                "state": "COMPLETED",
                "process_created": False,
                "direct_process_terminated_confirmed": True,
                "process_tree_terminated_confirmed": True,
                "client_abandoned": False,
            },
        ),
    })
    with pytest.raises(router.AllProvidersFailed, match="FALSO"):
        router.run_combo("t", "prompt", verify=True)


def test_run_combo_verify_unavailable_accepts_with_warning(monkeypatch):
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    monkeypatch.setattr(router, "ask_provider", lambda p, pr, **kw: _ok(p))
    _patch_verify(monkeypatch, {
        "p1": lambda execution_id, attempt_id: Verdict(
            verdadeiro=None,
            motivos=["sem verificador"],
            execution={
                "execution_id": execution_id,
                "attempt_id": attempt_id,
                "state": "COMPLETED",
                "process_created": False,
                "direct_process_terminated_confirmed": True,
                "process_tree_terminated_confirmed": True,
                "client_abandoned": False,
            },
        ),
    })
    result = router.run_combo("t", "prompt", verify=True)
    assert result.exit_code == 0
    assert any("sem checagem" in w for w in result.warnings)


def test_run_combo_without_verify_unchanged(monkeypatch):
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    monkeypatch.setattr(router, "ask_provider", lambda p, pr, **kw: _ok(p))
    result = router.run_combo("t", "prompt")
    assert result.exit_code == 0
    assert result.verdict is None


def test_ask_provider_verified_cancelled_verifier_returns_phase_execution_without_retry(monkeypatch):
    calls = {"ask": 0, "verify": 0}

    def fake_ask(*_args, **_kwargs):
        calls["ask"] += 1
        return RunResult(
            provider="p1",
            command=[],
            output="ok",
            exit_code=0,
            execution={
                "execution_id": _kwargs.get("execution_id"),
                "attempt_id": _kwargs.get("attempt_id"),
                "state": "COMPLETED",
                "process_created": True,
                "direct_process_terminated_confirmed": True,
                "process_tree_terminated_confirmed": True,
                "pgid": 123,
            },
        )

    def fake_verify(*_args, **kwargs):
        calls["verify"] += 1
        return Verdict(
            verdadeiro=None,
            motivos=["cancelled"],
            execution={
                "execution_id": kwargs.get("execution_id"),
                "attempt_id": kwargs.get("attempt_id"),
                "state": "CANCELLED",
                "termination_reason": "user_requested",
            },
        )

    monkeypatch.setattr(providers, "ask_provider", fake_ask)
    monkeypatch.setattr("athena.verifier.verify_report", fake_verify)
    monkeypatch.setattr("athena.reliability.record_verdict", lambda *a, **k: None)

    result = providers.ask_provider_verified("p1", "prompt")
    assert calls["ask"] == 1
    assert calls["verify"] == 1
    assert result.exit_code == 130
    assert result.error == "user_requested"
    assert result.execution is not None
    assert result.execution["state"] == "CANCELLED"


def test_ask_provider_verified_unconfirmed_verifier_returns_125_without_retry(monkeypatch):
    calls = {"ask": 0, "verify": 0}

    def fake_ask(*_args, **_kwargs):
        calls["ask"] += 1
        return RunResult(
            provider="p1",
            command=[],
            output="ok",
            exit_code=0,
            execution={
                "execution_id": _kwargs.get("execution_id"),
                "attempt_id": _kwargs.get("attempt_id"),
                "state": "COMPLETED",
                "process_created": True,
                "direct_process_terminated_confirmed": True,
                "process_tree_terminated_confirmed": True,
                "pgid": 123,
            },
        )

    def fake_verify(*_args, **kwargs):
        calls["verify"] += 1
        return Verdict(
            verdadeiro=None,
            motivos=["unconfirmed"],
            execution={
                "execution_id": kwargs.get("execution_id"),
                "attempt_id": kwargs.get("attempt_id"),
                "state": "TERMINATION_UNCONFIRMED",
            },
        )

    monkeypatch.setattr(providers, "ask_provider", fake_ask)
    monkeypatch.setattr("athena.verifier.verify_report", fake_verify)
    monkeypatch.setattr("athena.reliability.record_verdict", lambda *a, **k: None)

    result = providers.ask_provider_verified("p1", "prompt")
    assert calls["ask"] == 1
    assert calls["verify"] == 1
    assert result.exit_code == 125
    assert result.error == "verifier_termination_unconfirmed"
    assert result.execution is not None
    assert result.execution["state"] == "TERMINATION_UNCONFIRMED"


def test_ask_provider_verified_ssh_skips_verifier_and_retry(monkeypatch):
    calls = {"ask": 0, "verify": 0}

    def fake_ask(*_args, **_kwargs):
        calls["ask"] += 1
        return RunResult(
            provider="p1",
            command=[],
            output="ok",
            exit_code=0,
            execution={
                "execution_id": _kwargs.get("execution_id"),
                "attempt_id": _kwargs.get("attempt_id"),
                "state": "COMPLETED",
                "process_created": True,
                "direct_process_terminated_confirmed": True,
                "process_tree_terminated_confirmed": True,
                "pgid": 123,
            },
        )

    def fake_verify(*_args, **_kwargs):
        calls["verify"] += 1
        return Verdict(verdadeiro=True, motivos=["should not run"], verificador="deterministic")

    monkeypatch.setattr(providers, "ask_provider", fake_ask)
    monkeypatch.setattr("athena.verifier.verify_report", fake_verify)
    monkeypatch.setattr("athena.reliability.record_verdict", lambda *a, **k: None)

    result = providers.ask_provider_verified("p1", "prompt", ssh_host="builder")
    assert calls["ask"] == 1
    assert calls["verify"] == 0
    assert result.exit_code == 0
    assert any("verification_unavailable_remote" in w for w in result.warnings)
    assert result.verdict is not None
    assert result.verdict["verdadeiro"] is None


def test_ask_provider_ssh_invalid_host_is_prelaunch_failure(monkeypatch):
    monkeypatch.setitem(
        providers.PROVIDERS,
        "sshsynthetic",
        ProviderSpec(
            id="sshsynthetic",
            name="SSH Synthetic",
            binary="dummy-cli",
            description="synthetic",
        ),
    )
    monkeypatch.setattr(providers, "resolve_binary", lambda _spec: "/bin/echo")
    result = providers.ask_provider("sshsynthetic", "prompt", ssh_host="-bad-option")
    assert result.exit_code == 2
    assert result.execution is not None
    assert result.execution["transport"] == "ssh"
    assert result.execution["process_created"] is False
    assert result.execution["remote_session_started"] is False


def test_ask_provider_ssh_metadata_does_not_include_host_or_user(monkeypatch):
    monkeypatch.setitem(
        providers.PROVIDERS,
        "sshsyntheticok",
        ProviderSpec(
            id="sshsyntheticok",
            name="SSH Synthetic OK",
            binary="dummy-cli",
            description="synthetic",
        ),
    )
    monkeypatch.setattr(providers, "resolve_binary", lambda _spec: "/bin/echo")

    def fake_run_subprocess(*_args, **kwargs):
        execution = {
            "execution_id": kwargs.get("execution_id") or "e",
            "attempt_id": kwargs.get("attempt_id") or "a",
            "provider": "sshsyntheticok",
            "state": "COMPLETED",
            "transport": "ssh",
            "process_created": True,
            "remote_session_started": True,
            "remote_termination_confirmed": False,
            "direct_process_terminated_confirmed": True,
            "process_tree_terminated_confirmed": True,
        }
        return RunResult(
            provider="sshsyntheticok",
            command=[],
            output="ok",
            exit_code=0,
            execution=execution,
        )

    monkeypatch.setattr(providers, "run_subprocess", fake_run_subprocess)
    result = providers.ask_provider("sshsyntheticok", "prompt", ssh_host="dev@safe-host")
    assert result.execution is not None
    serialized = str(result.execution)
    assert "safe-host" not in serialized
    assert "dev@" not in serialized


# --- confiabilidade no recommend ---


def _fake_catalog():
    return {
        "modeloa modeloa": [
            {"provider": "mentiroso", "provider_name": "M", "model_id": "modeloa", "model_name": "ModeloA", "weight": "medium"},
        ],
        "modelob modelob": [
            {"provider": "honesto", "provider_name": "H", "model_id": "modelob", "model_name": "ModeloB", "weight": "medium"},
        ],
    }


def _fake_ratings():
    return {
        "roles": rec.ROLE_LABELS,
        "models": [
            {"match": ["modeloa"], "name": "ModeloA", "maker": "X", "scores": {"backend": 9}, "note": "", "sources": []},
            {"match": ["modelob"], "name": "ModeloB", "maker": "X", "scores": {"backend": 8.5}, "note": "", "sources": []},
        ],
    }


def _fake_reliability():
    return {
        "mentiroso": {"verdadeiros": 1, "falsos": 4, "escalados": 2, "episodios": 5,
                       "taxa_falso": 0.8, "confiabilidade": 0.2, "indisponiveis": 0},
        "honesto": {"verdadeiros": 9, "falsos": 1, "escalados": 0, "episodios": 10,
                     "taxa_falso": 0.1, "confiabilidade": 0.9, "indisponiveis": 0},
    }


def test_recommend_penalizes_unreliable_provider(monkeypatch):
    monkeypatch.setattr(rec, "_providers_by_model", _fake_catalog)
    monkeypatch.setattr(rec, "load_ratings", _fake_ratings)
    monkeypatch.setattr("athena.reliability.reliability_report", _fake_reliability)
    r = rec.recommend_for_task("implementar endpoint na api", task_type="backend")
    nomes = [s["modelo"] for s in r["recomendacoes"]]
    # ModeloB tem nota menor (8.5 < 9) mas provider confiável deve vir primeiro
    assert nomes[0] == "ModeloB"
    rec_a = next(s for s in r["recomendacoes"] if s["modelo"] == "ModeloA")
    assert rec_a["avisos_confianca"]  # aviso de provider mentiroso
    assert "FALSOS" in rec_a["avisos_confianca"][0]


def test_recommend_without_history_keeps_public_scores(monkeypatch):
    monkeypatch.setattr(rec, "_providers_by_model", _fake_catalog)
    monkeypatch.setattr(rec, "load_ratings", _fake_ratings)
    monkeypatch.setattr("athena.reliability.reliability_report", lambda: {})
    r = rec.recommend_for_task("implementar endpoint na api", task_type="backend")
    assert r["recomendacoes"][0]["modelo"] == "ModeloA"  # nota pública manda
    assert "Sem histórico" in r["confianca"]


def test_recommend_use_reliability_false_ignores_history(monkeypatch):
    monkeypatch.setattr(rec, "_providers_by_model", _fake_catalog)
    monkeypatch.setattr(rec, "load_ratings", _fake_ratings)
    monkeypatch.setattr(
        "athena.reliability.reliability_report",
        lambda: (_ for _ in ()).throw(AssertionError("não deveria consultar")),
    )
    r = rec.recommend_for_task("implementar endpoint na api", task_type="backend", use_reliability=False)
    assert r["recomendacoes"][0]["modelo"] == "ModeloA"
