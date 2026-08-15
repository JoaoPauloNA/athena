from __future__ import annotations

import threading

import pytest

from athena import providers as providers_module
from athena import workspace_lease
from athena.bridge import RunResult
from athena.providers import ProviderSpec
from athena.verifier import Verdict


@pytest.fixture(autouse=True)
def _clean_workspace_lease_registry():
    workspace_lease._reset_for_tests()
    yield
    workspace_lease._reset_for_tests()


def _meta(execution_id: str, attempt_id: str, state: str = "COMPLETED") -> dict:
    return {
        "execution_id": execution_id,
        "attempt_id": attempt_id,
        "state": state,
        "process_created": True,
        "direct_process_terminated_confirmed": state != "TERMINATION_UNCONFIRMED",
        "process_tree_terminated_confirmed": state != "TERMINATION_UNCONFIRMED",
        "pgid": 123,
        "client_abandoned": False,
    }


def _setup_provider(monkeypatch, provider_id: str = "synthetic") -> None:
    monkeypatch.setitem(
        providers_module.PROVIDERS,
        provider_id,
        ProviderSpec(
            id=provider_id,
            name="Synthetic",
            binary="synthetic-bin",
            description="synthetic",
            default_timeout=30,
        ),
    )
    monkeypatch.setattr(providers_module, "resolve_binary", lambda _spec: "/bin/echo")
    # Lease tests must not depend on the state or refresh latency of the
    # user-level model catalog.  A fresh ATHENA_DATA_DIR can otherwise spend
    # longer than this test's synchronization window discovering models,
    # before the synthetic runner is reached.
    monkeypatch.setattr(
        providers_module,
        "resolve_model",
        lambda _provider_id, requested: requested,
    )


def test_direct_ask_concurrent_same_workspace_blocks_second(monkeypatch, tmp_path):
    _setup_provider(monkeypatch)
    ws = str(tmp_path)
    gate = threading.Event()
    started = threading.Event()

    def fake_run_subprocess(provider, command, **kwargs):
        started.set()
        gate.wait(timeout=2)
        return RunResult(
            provider=provider,
            command=command,
            output="ok",
            exit_code=0,
            execution=_meta(kwargs["execution_id"], kwargs["attempt_id"], "COMPLETED"),
        )

    monkeypatch.setattr(providers_module, "run_subprocess", fake_run_subprocess)
    first_result: dict[str, object] = {}
    thread_error: list[Exception] = []

    def _first():
        try:
            first_result["value"] = providers_module.ask_provider(
                "synthetic", "prompt", working_directory=ws
            )
        except Exception as exc:  # pragma: no cover - assertion path
            thread_error.append(exc)

    t = threading.Thread(target=_first, daemon=True)
    t.start()
    assert started.wait(timeout=1.0)
    with pytest.raises(workspace_lease.WorkspaceLeaseError):
        providers_module.ask_provider("synthetic", "prompt-2", working_directory=ws)
    gate.set()
    t.join(timeout=2.0)
    assert not thread_error
    assert workspace_lease.current_holder(ws) is None


def test_direct_ask_release_policy_confirmed_vs_unconfirmed_and_prelaunch(monkeypatch, tmp_path):
    _setup_provider(monkeypatch)
    ws = str(tmp_path)

    monkeypatch.setattr(
        providers_module,
        "run_subprocess",
        lambda provider, command, **kwargs: RunResult(
            provider=provider,
            command=command,
            output="ok",
            exit_code=0,
            execution=_meta(kwargs["execution_id"], kwargs["attempt_id"], "COMPLETED"),
        ),
    )
    ok = providers_module.ask_provider("synthetic", "prompt", working_directory=ws)
    assert ok.exit_code == 0
    assert workspace_lease.current_holder(ws) is None

    monkeypatch.setattr(
        providers_module,
        "run_subprocess",
        lambda provider, command, **kwargs: RunResult(
            provider=provider,
            command=command,
            output="",
            exit_code=125,
            error="unconfirmed",
            execution=_meta(kwargs["execution_id"], kwargs["attempt_id"], "TERMINATION_UNCONFIRMED"),
        ),
    )
    bad = providers_module.ask_provider("synthetic", "prompt", working_directory=ws)
    assert bad.exit_code == 125
    assert workspace_lease.current_holder(ws) is not None

    workspace_lease._reset_for_tests()
    with pytest.raises(ValueError):
        providers_module.ask_provider("synthetic", "prompt", working_directory=ws, timeout=0)
    assert workspace_lease.current_holder(ws) is None


def test_usage_oserror_is_best_effort_after_valid_result(monkeypatch, tmp_path):
    _setup_provider(monkeypatch)
    ws = str(tmp_path)

    monkeypatch.setattr(
        providers_module,
        "run_subprocess",
        lambda provider, command, **kwargs: RunResult(
            provider=provider,
            command=command,
            output="ok",
            exit_code=0,
            execution=_meta(kwargs["execution_id"], kwargs["attempt_id"], "COMPLETED"),
        ),
    )

    def fail_usage(*_args, **_kwargs):
        raise OSError("synthetic usage storage unavailable")

    monkeypatch.setattr(providers_module, "record_usage", fail_usage)
    result = providers_module.ask_provider("synthetic", "prompt", working_directory=ws)

    assert result.exit_code == 0
    assert "usage_telemetry_unavailable" in result.warnings
    assert workspace_lease.current_holder(ws) is None


def test_ask_provider_reentrant_router_holder_not_released(monkeypatch, tmp_path):
    _setup_provider(monkeypatch)
    ws = str(tmp_path)
    execution_id = "exec-router"
    attempt_id = "attempt-router"
    workspace_lease.acquire(ws, execution_id, attempt_id)

    monkeypatch.setattr(
        providers_module,
        "run_subprocess",
        lambda provider, command, **kwargs: RunResult(
            provider=provider,
            command=command,
            output="ok",
            exit_code=0,
            execution=_meta(kwargs["execution_id"], kwargs["attempt_id"], "COMPLETED"),
        ),
    )
    result = providers_module.ask_provider(
        "synthetic",
        "prompt",
        working_directory=ws,
        execution_id=execution_id,
        attempt_id=attempt_id,
    )
    assert result.exit_code == 0
    assert workspace_lease.current_holder(ws) == (execution_id, attempt_id)


def test_verified_success_transfers_executor_to_verifier_and_releases(monkeypatch, tmp_path):
    ws = str(tmp_path)
    attempts_seen: list[str] = []

    def fake_ask(_provider_id, _prompt, **kwargs):
        attempts_seen.append(kwargs["attempt_id"])
        workspace_lease.acquire_for_scope(ws, kwargs["execution_id"], kwargs["attempt_id"])
        return RunResult(
            provider="synthetic",
            command=[],
            output="ok",
            exit_code=0,
            execution=_meta(kwargs["execution_id"], kwargs["attempt_id"], "COMPLETED"),
        )

    def fake_verify(*_args, **kwargs):
        attempts_seen.append(kwargs["attempt_id"])
        assert workspace_lease.current_holder(ws) == (kwargs["execution_id"], kwargs["attempt_id"])
        return Verdict(
            verdadeiro=True,
            confianca="alta",
            motivos=["ok"],
            verificador="deterministic",
            execution=_meta(kwargs["execution_id"], kwargs["attempt_id"], "COMPLETED"),
        )

    monkeypatch.setattr(providers_module, "ask_provider", fake_ask)
    monkeypatch.setattr("athena.verifier.verify_report", fake_verify)
    monkeypatch.setattr("athena.reliability.record_verdict", lambda *_a, **_k: None)

    result = providers_module.ask_provider_verified(
        "synthetic", "prompt", working_directory=ws
    )
    assert result.verdict is not None
    assert result.verdict["verdadeiro"] is True
    assert len(set(attempts_seen)) == 2
    assert workspace_lease.current_holder(ws) is None


def test_verified_false_retry_uses_four_distinct_attempt_ids(monkeypatch, tmp_path):
    ws = str(tmp_path)
    attempt_ids: list[str] = []
    verify_calls = {"count": 0}

    def fake_ask(_provider_id, _prompt, **kwargs):
        attempt_ids.append(kwargs["attempt_id"])
        workspace_lease.acquire_for_scope(ws, kwargs["execution_id"], kwargs["attempt_id"])
        return RunResult(
            provider="synthetic",
            command=[],
            output=f"out-{len(attempt_ids)}",
            exit_code=0,
            execution=_meta(kwargs["execution_id"], kwargs["attempt_id"], "COMPLETED"),
        )

    def fake_verify(*_args, **kwargs):
        attempt_ids.append(kwargs["attempt_id"])
        verify_calls["count"] += 1
        return Verdict(
            verdadeiro=(verify_calls["count"] >= 2),
            confianca="alta",
            motivos=["false-1"] if verify_calls["count"] == 1 else ["ok"],
            verificador="deterministic",
            execution=_meta(kwargs["execution_id"], kwargs["attempt_id"], "COMPLETED"),
        )

    monkeypatch.setattr(providers_module, "ask_provider", fake_ask)
    monkeypatch.setattr("athena.verifier.verify_report", fake_verify)
    monkeypatch.setattr("athena.reliability.record_verdict", lambda *_a, **_k: None)

    result = providers_module.ask_provider_verified(
        "synthetic", "prompt", working_directory=ws
    )
    assert result.verdict is not None
    assert result.verdict["verdadeiro"] is True
    assert len(attempt_ids) == 4
    assert len(set(attempt_ids)) == 4
    assert workspace_lease.current_holder(ws) is None


def test_verified_transfer_blocked_on_unconfirmed_metadata(monkeypatch, tmp_path):
    ws = str(tmp_path)

    def fake_ask(_provider_id, _prompt, **kwargs):
        workspace_lease.acquire_for_scope(ws, kwargs["execution_id"], kwargs["attempt_id"])
        return RunResult(
            provider="synthetic",
            command=[],
            output="ok",
            exit_code=0,
            execution=_meta(kwargs["execution_id"], kwargs["attempt_id"], "TERMINATION_UNCONFIRMED"),
        )

    monkeypatch.setattr(providers_module, "ask_provider", fake_ask)
    monkeypatch.setattr("athena.reliability.record_verdict", lambda *_a, **_k: None)

    with pytest.raises(workspace_lease.WorkspaceLeaseError):
        providers_module.ask_provider_verified("synthetic", "prompt", working_directory=ws)
    assert workspace_lease.current_holder(ws) is not None


@pytest.mark.parametrize("state", ["FAILED", "CANCELLED", "TIMED_OUT"])
def test_direct_ask_confirmed_terminal_state_releases(monkeypatch, tmp_path, state):
    _setup_provider(monkeypatch)
    ws = str(tmp_path)
    monkeypatch.setattr(
        providers_module,
        "run_subprocess",
        lambda provider, command, **kwargs: RunResult(
            provider=provider,
            command=command,
            output="",
            exit_code=1,
            error="terminal",
            execution=_meta(kwargs["execution_id"], kwargs["attempt_id"], state),
        ),
    )
    result = providers_module.ask_provider("synthetic", "prompt", working_directory=ws)
    assert result.exit_code == 1
    assert workspace_lease.current_holder(ws) is None


def test_direct_ask_without_execution_metadata_retains_and_blocks_second(monkeypatch, tmp_path):
    _setup_provider(monkeypatch)
    ws = str(tmp_path)
    monkeypatch.setattr(
        providers_module,
        "run_subprocess",
        lambda provider, command, **_kwargs: RunResult(
            provider=provider,
            command=command,
            output="ok",
            exit_code=0,
            execution=None,
        ),
    )
    first = providers_module.ask_provider("synthetic", "prompt", working_directory=ws)
    assert first.execution is None
    assert workspace_lease.current_holder(ws) is not None
    with pytest.raises(workspace_lease.WorkspaceLeaseError):
        providers_module.ask_provider("synthetic", "prompt-2", working_directory=ws)


def test_direct_ask_remote_session_without_confirmation_retains(monkeypatch, tmp_path):
    _setup_provider(monkeypatch)
    ws = str(tmp_path)
    monkeypatch.setattr(
        providers_module,
        "run_subprocess",
        lambda provider, command, **kwargs: RunResult(
            provider=provider,
            command=command,
            output="ok",
            exit_code=0,
            execution={
                **_meta(kwargs["execution_id"], kwargs["attempt_id"], "COMPLETED"),
                "transport": "ssh",
                "remote_session_started": True,
                "remote_termination_confirmed": False,
            },
        ),
    )
    providers_module.ask_provider("synthetic", "prompt", working_directory=ws)
    assert workspace_lease.current_holder(ws) is not None


@pytest.mark.parametrize("executor_execution", [None, {"state": "TERMINATION_UNCONFIRMED"}])
def test_verified_without_workspace_blocks_before_verify_on_unsafe_executor_meta(
    monkeypatch,
    executor_execution,
):
    verify_called = {"value": 0}

    def fake_ask(_provider_id, _prompt, **_kwargs):
        return RunResult(
            provider="synthetic",
            command=[],
            output="ok",
            exit_code=0,
            execution=executor_execution,
        )

    def fake_verify(*_args, **_kwargs):
        verify_called["value"] += 1
        return Verdict(verdadeiro=True, verificador="deterministic", execution={"state": "COMPLETED"})

    monkeypatch.setattr(providers_module, "ask_provider", fake_ask)
    monkeypatch.setattr("athena.verifier.verify_report", fake_verify)
    monkeypatch.setattr("athena.reliability.record_verdict", lambda *_a, **_k: None)

    with pytest.raises(workspace_lease.WorkspaceLeaseError):
        providers_module.ask_provider_verified("synthetic", "prompt", working_directory=None)
    assert verify_called["value"] == 0


@pytest.mark.parametrize(
    "verdict_execution",
    [
        None,
        {
            "execution_id": "wrong-exec",
            "attempt_id": "wrong-attempt",
            "state": "COMPLETED",
            "process_created": False,
            "direct_process_terminated_confirmed": True,
            "process_tree_terminated_confirmed": True,
            "pgid": None,
        },
    ],
)
def test_verified_false_with_unsafe_verdict_meta_does_not_retry(monkeypatch, tmp_path, verdict_execution):
    ws = str(tmp_path)
    executor_calls = {"count": 0}
    verify_calls = {"count": 0}

    def fake_ask(_provider_id, _prompt, **kwargs):
        executor_calls["count"] += 1
        workspace_lease.acquire_for_scope(ws, kwargs["execution_id"], kwargs["attempt_id"])
        return RunResult(
            provider="synthetic",
            command=[],
            output="executor-report",
            exit_code=0,
            execution=_meta(kwargs["execution_id"], kwargs["attempt_id"], "COMPLETED"),
        )

    def fake_verify(*_args, **kwargs):
        verify_calls["count"] += 1
        return Verdict(
            verdadeiro=False,
            confianca="alta",
            motivos=["bad"],
            verificador="deterministic",
            execution=verdict_execution,
        )

    monkeypatch.setattr(providers_module, "ask_provider", fake_ask)
    monkeypatch.setattr("athena.verifier.verify_report", fake_verify)
    monkeypatch.setattr("athena.reliability.record_verdict", lambda *_a, **_k: None)

    with pytest.raises(workspace_lease.WorkspaceLeaseError):
        providers_module.ask_provider_verified("synthetic", "prompt", working_directory=ws)
    assert verify_calls["count"] == 1
    assert executor_calls["count"] == 1
    assert workspace_lease.current_holder(ws) is not None


def test_verified_uses_provided_attempt_id_for_initial_executor(monkeypatch, tmp_path):
    ws = str(tmp_path)
    seen_attempts: list[str] = []
    provided_attempt_id = "attempt-provided"

    def fake_ask(_provider_id, _prompt, **kwargs):
        seen_attempts.append(kwargs["attempt_id"])
        workspace_lease.acquire_for_scope(ws, kwargs["execution_id"], kwargs["attempt_id"])
        return RunResult(
            provider="synthetic",
            command=[],
            output="ok",
            exit_code=0,
            execution=_meta(kwargs["execution_id"], kwargs["attempt_id"], "COMPLETED"),
        )

    def fake_verify(*_args, **kwargs):
        seen_attempts.append(kwargs["attempt_id"])
        return Verdict(
            verdadeiro=True,
            confianca="alta",
            motivos=["ok"],
            verificador="deterministic",
            execution=_meta(kwargs["execution_id"], kwargs["attempt_id"], "COMPLETED"),
        )

    monkeypatch.setattr(providers_module, "ask_provider", fake_ask)
    monkeypatch.setattr("athena.verifier.verify_report", fake_verify)
    monkeypatch.setattr("athena.reliability.record_verdict", lambda *_a, **_k: None)

    providers_module.ask_provider_verified(
        "synthetic",
        "prompt",
        working_directory=ws,
        attempt_id=provided_attempt_id,
    )
    assert seen_attempts[0] == provided_attempt_id


def test_verified_real_ask_provider_retains_transfers_and_releases(monkeypatch, tmp_path):
    _setup_provider(monkeypatch)
    ws = str(tmp_path)
    call = {"count": 0}

    def fake_run_subprocess(provider, command, **kwargs):
        call["count"] += 1
        state = "COMPLETED"
        return RunResult(
            provider=provider,
            command=command,
            output=f"out-{call['count']}",
            exit_code=0,
            error=None,
            execution=_meta(kwargs["execution_id"], kwargs["attempt_id"], state),
        )

    verify_calls = {"count": 0}

    def fake_verify(*_args, **kwargs):
        verify_calls["count"] += 1
        holder = workspace_lease.current_holder(ws)
        assert holder is not None
        assert holder == (kwargs["execution_id"], kwargs["attempt_id"])
        return Verdict(
            verdadeiro=verify_calls["count"] > 1,
            confianca="alta",
            motivos=["first false"] if verify_calls["count"] == 1 else ["ok"],
            verificador="deterministic",
            execution=_meta(kwargs["execution_id"], kwargs["attempt_id"], "COMPLETED"),
        )

    monkeypatch.setattr(providers_module, "run_subprocess", fake_run_subprocess)
    monkeypatch.setattr("athena.verifier.verify_report", fake_verify)
    monkeypatch.setattr("athena.reliability.record_verdict", lambda *_a, **_k: None)

    result = providers_module.ask_provider_verified("synthetic", "prompt", working_directory=ws)
    assert result.verdict is not None
    assert result.verdict["verdadeiro"] is True
    assert workspace_lease.current_holder(ws) is None
