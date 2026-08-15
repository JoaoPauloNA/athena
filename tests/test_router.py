"""Testes do router (failover de combos)."""
import sys

import pytest

import athena.providers as providers_module
from athena import router, workspace_lease
from athena.bridge import RunResult, run_subprocess
from athena.combos import Combo, ComboStep, FailoverPolicy
from athena.providers import ProviderSpec
from athena.verifier import Verdict


@pytest.fixture(autouse=True)
def _clean_workspace_lease_registry():
    """Sprint A6: the workspace lease registry is process-global state;
    reset it around every test so tests never leak leases into each other."""
    workspace_lease._reset_for_tests()
    yield
    workspace_lease._reset_for_tests()


def _ok(provider):
    return RunResult(provider=provider, command=[], output="ok", exit_code=0)


def _ok_with_execution(provider, execution_id, attempt_id):
    return RunResult(
        provider=provider,
        command=[],
        output="ok",
        exit_code=0,
        execution=_bind_meta(
            _exec_meta("COMPLETED", direct_confirmed=True, tree_confirmed=True, pgid=123),
            execution_id,
            attempt_id,
        ),
    )


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


# --- Sprint A5: fallback/retry gated on execution lifecycle metadata -------


def _exec_meta(
    state,
    *,
    direct_confirmed=True,
    tree_confirmed=True,
    pgid=None,
    client_abandoned=False,
    process_created=True,
):
    return {
        "state": state,
        "direct_process_terminated_confirmed": direct_confirmed,
        "process_tree_terminated_confirmed": tree_confirmed,
        "pgid": pgid,
        "client_abandoned": client_abandoned,
        "process_created": process_created,
    }


def _bind_meta(meta, execution_id, attempt_id):
    bound = dict(meta)
    bound["execution_id"] = execution_id
    bound["attempt_id"] = attempt_id
    return bound


def _verifier_meta(execution_id, attempt_id, state="COMPLETED"):
    return {
        "execution_id": execution_id,
        "attempt_id": attempt_id,
        "provider": "verifier",
        "profile": "verification",
        "state": state,
        "process_created": False,
        "direct_process_terminated_confirmed": True,
        "process_tree_terminated_confirmed": True,
        "client_abandoned": False,
    }


def _timeout(provider, execution=None):
    return RunResult(
        provider=provider,
        command=[],
        output="",
        exit_code=124,
        timed_out=True,
        error="timeout",
        execution=execution,
    )


def _fail_with_execution(provider, execution=None):
    return RunResult(
        provider=provider,
        command=[],
        output="",
        exit_code=1,
        error="boom",
        execution=execution,
    )


def test_unconfirmed_timeout_blocks_fallback(monkeypatch):
    """TERMINATION_UNCONFIRMED must never allow a second attempt to start —
    the first attempt's process might still be alive."""
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    calls = []

    def fake_ask(provider_id, prompt, **kw):
        calls.append(provider_id)
        return _timeout(provider_id, _exec_meta("TERMINATION_UNCONFIRMED"))

    monkeypatch.setattr(router, "ask_provider", fake_ask)

    with pytest.raises(router.FallbackBlocked):
        router.run_combo("t", "prompt")

    # Only the first provider may ever have been attempted — the second
    # provider (p2) must never be started while termination is unconfirmed.
    assert calls == ["p1"]


def test_unconfirmed_timeout_blocked_outcome_is_also_all_providers_failed(monkeypatch):
    """FallbackBlocked is a distinguishable subclass of AllProvidersFailed so
    existing callers that only catch the base class keep working."""
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    monkeypatch.setattr(
        router, "ask_provider", lambda p, pr, **kw: _timeout(p, _exec_meta("TERMINATION_UNCONFIRMED"))
    )
    with pytest.raises(router.AllProvidersFailed):
        router.run_combo("t", "prompt")


def test_confirmed_cancelled_timeout_proceeds_to_fallback(monkeypatch):
    """CANCELLED with confirmed direct+tree termination is safe to fall
    back from — the second provider must run."""
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    calls = []

    def fake_ask(provider_id, prompt, **kw):
        calls.append(provider_id)
        if provider_id == "p1":
            return _timeout(provider_id, _exec_meta("CANCELLED", pgid=123))
        return _ok(provider_id)

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    result = router.run_combo("t", "prompt")
    assert result.exit_code == 0
    assert calls == ["p1", "p2"]


def test_confirmed_failed_proceeds_to_fallback(monkeypatch):
    """FAILED with confirmed termination may fall back per policy
    (on_error=True is the default)."""
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    calls = []

    def fake_ask(provider_id, prompt, **kw):
        calls.append(provider_id)
        if provider_id == "p1":
            return _fail_with_execution(provider_id, _exec_meta("FAILED"))
        return _ok(provider_id)

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    result = router.run_combo("t", "prompt")
    assert result.exit_code == 0
    assert calls == ["p1", "p2"]


def test_failed_with_unconfirmed_tree_termination_blocks_fallback(monkeypatch):
    """FAILED with a confirmed direct kill but an unconfirmed process-group
    teardown must still block — descendants may still be running."""
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    calls = []

    def fake_ask(provider_id, prompt, **kw):
        calls.append(provider_id)
        return _fail_with_execution(
            provider_id,
            _exec_meta("FAILED", direct_confirmed=True, tree_confirmed=False, pgid=123),
        )

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    with pytest.raises(router.FallbackBlocked):
        router.run_combo("t", "prompt")
    assert calls == ["p1"]


def test_ssh_failed_without_remote_termination_confirmation_blocks_fallback(monkeypatch):
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    calls = []

    def fake_ask(provider_id, prompt, **kw):
        calls.append(provider_id)
        return _fail_with_execution(
            provider_id,
            _exec_meta(
                "FAILED",
                direct_confirmed=True,
                tree_confirmed=True,
                pgid=123,
            )
            | {
                "remote_session_started": True,
                "remote_termination_confirmed": False,
                "transport": "ssh",
            },
        )

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    with pytest.raises(router.FallbackBlocked):
        router.run_combo("t", "prompt")
    assert calls == ["p1"]


def test_failed_with_no_process_created_proceeds_to_fallback(monkeypatch):
    """FAILED with process_created=False (Popen() itself raised, e.g.
    FileNotFoundError for a missing command) must fall back without
    requiring termination confirmation — no process ever existed to
    confirm terminated."""
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    calls = []

    def fake_ask(provider_id, prompt, **kw):
        calls.append(provider_id)
        if provider_id == "p1":
            return _fail_with_execution(
                provider_id,
                _exec_meta(
                    "FAILED",
                    direct_confirmed=False,
                    tree_confirmed=False,
                    process_created=False,
                ),
            )
        return _ok(provider_id)

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    result = router.run_combo("t", "prompt")
    assert result.exit_code == 0
    assert calls == ["p1", "p2"]


def test_cancelled_timeout_with_no_process_created_proceeds_to_fallback(monkeypatch):
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    calls = []

    def fake_ask(provider_id, prompt, **kw):
        calls.append(provider_id)
        if provider_id == "p1":
            return _timeout(
                provider_id,
                _exec_meta(
                    "CANCELLED",
                    direct_confirmed=False,
                    tree_confirmed=False,
                    process_created=False,
                ),
            )
        return _ok(provider_id)

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    result = router.run_combo("t", "prompt")
    assert result.exit_code == 0
    assert calls == ["p1", "p2"]


def test_client_abandoned_blocks_fallback_until_explicit_policy(monkeypatch):
    """client_abandoned forbids fallback even when termination is otherwise
    confirmed — there is no explicit policy (yet) authorizing it."""
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    calls = []

    def fake_ask(provider_id, prompt, **kw):
        calls.append(provider_id)
        return _fail_with_execution(
            provider_id, _exec_meta("FAILED", client_abandoned=True)
        )

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    with pytest.raises(router.FallbackBlocked):
        router.run_combo("t", "prompt")
    assert calls == ["p1"]


def test_completed_execution_metadata_is_defense_in_depth_no_fallback(monkeypatch):
    """A COMPLETED execution record accompanying an error result (should not
    happen via the real transport, but is possible from a buggy/mocked
    caller) must not be treated as safe to retry from."""
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    monkeypatch.setattr(
        router, "ask_provider", lambda p, pr, **kw: _fail_with_execution(p, _exec_meta("COMPLETED"))
    )
    with pytest.raises(router.FallbackBlocked):
        router.run_combo("t", "prompt")


def test_legacy_result_without_execution_metadata_keeps_existing_policy_behavior(monkeypatch):
    """RunResult.execution is None (legacy/mocked, no lifecycle contract
    collected) — existing policy-only failover behavior is preserved
    unchanged, exactly like test_failover_to_second_provider above."""
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    calls = []

    def fake_ask(provider_id, prompt, **kw):
        calls.append(provider_id)
        return _fail(provider_id) if provider_id == "p1" else _ok(provider_id)

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    result = router.run_combo("t", "prompt")
    assert result.exit_code == 0
    assert calls == ["p1", "p2"]


def test_real_synthetic_nonzero_exit_allows_fallback_to_second_provider(monkeypatch):
    """Integration: a *real* run_subprocess() nonzero-exit result (not a
    hand-built execution dict) must carry execution metadata that the
    router accepts as confirmed-terminated, so failover to the next
    provider in the combo actually proceeds instead of raising
    FallbackBlocked."""
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    calls = []

    def fake_ask(provider_id, prompt, **kw):
        calls.append(provider_id)
        if provider_id == "p1":
            return run_subprocess(
                provider_id,
                [sys.executable, "-c", "import sys; sys.exit(3)"],
                timeout=5,
            )
        return _ok(provider_id)

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    result = router.run_combo("t", "prompt")
    assert result.exit_code == 0
    assert calls == ["p1", "p2"]


def test_real_missing_command_allows_fallback_to_second_provider(monkeypatch):
    """Integration: a *real* run_subprocess() FileNotFoundError result (a
    command that does not exist, so Popen() never created a process) must
    carry execution metadata the router accepts as safe to fall back
    from — the second provider in the combo actually runs instead of
    raising FallbackBlocked."""
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    calls = []

    def fake_ask(provider_id, prompt, **kw):
        calls.append(provider_id)
        if provider_id == "p1":
            return run_subprocess(
                provider_id,
                ["athena-mcp-nonexistent-command-xyz"],
                timeout=5,
            )
        return _ok(provider_id)

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    result = router.run_combo("t", "prompt")
    assert result.exit_code == 0
    assert calls == ["p1", "p2"]


def test_on_timeout_false_returns_before_evaluating_block_reason(monkeypatch):
    """When the combo policy already forbids fallback on timeout, the router
    must return the timeout result directly — it must not raise
    FallbackBlocked even for an unconfirmed termination, since no second
    attempt was ever going to start."""
    combo = Combo(
        id="t",
        name="t",
        chain=[ComboStep(provider_id="p1"), ComboStep(provider_id="p2")],
        failover_policy=FailoverPolicy(on_timeout=False),
    )
    monkeypatch.setattr(router, "get_combo", lambda cid: combo)
    calls = []

    def fake_ask(provider_id, prompt, **kw):
        calls.append(provider_id)
        return _timeout(provider_id, _exec_meta("TERMINATION_UNCONFIRMED"))

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    result = router.run_combo("t", "prompt")
    assert result.timed_out is True
    assert calls == ["p1"]


# --- Sprint A6: run_combo() integration with athena.workspace_lease --------


def test_workspace_lease_blocks_concurrent_execution_on_same_workspace(monkeypatch, tmp_path):
    """A workspace already leased by another (in-process) execution must
    make run_combo() refuse to start the first provider at all."""
    ws = str(tmp_path)
    workspace_lease.acquire(ws, "some-other-execution", "attempt-1")

    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    calls = []

    def fake_ask(provider_id, prompt, **kw):
        calls.append(provider_id)
        return _ok(provider_id)

    monkeypatch.setattr(router, "ask_provider", fake_ask)

    with pytest.raises(workspace_lease.WorkspaceLeaseError):
        router.run_combo("t", "prompt", working_directory=ws)

    # The provider must never have been started while the workspace was
    # leased elsewhere.
    assert calls == []


def test_workspace_lease_released_after_successful_run(monkeypatch, tmp_path):
    ws = str(tmp_path)
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    monkeypatch.setattr(
        router,
        "ask_provider",
        lambda p, pr, **kw: _ok_with_execution(p, kw["execution_id"], kw["attempt_id"]),
    )

    result = router.run_combo("t", "prompt", working_directory=ws)

    assert result.exit_code == 0
    assert workspace_lease.current_holder(ws) is None


def test_workspace_lease_retained_when_unconfirmed_termination_blocks_fallback(
    monkeypatch, tmp_path
):
    """When TERMINATION_UNCONFIRMED blocks the fallback (see
    test_unconfirmed_timeout_blocks_fallback above), the workspace lease
    must stay held -- a second execution must still be refused the same
    workspace afterward, since the first attempt's process might still be
    running."""
    ws = str(tmp_path)
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())

    def fake_ask(provider_id, prompt, **kw):
        return _timeout(provider_id, _exec_meta("TERMINATION_UNCONFIRMED"))

    monkeypatch.setattr(router, "ask_provider", fake_ask)

    with pytest.raises(router.FallbackBlocked):
        router.run_combo("t", "prompt", working_directory=ws)

    assert workspace_lease.current_holder(ws) is not None
    with pytest.raises(workspace_lease.WorkspaceLeaseError):
        workspace_lease.acquire(ws, "another-execution", "attempt-1")


def test_workspace_lease_different_workspaces_allowed_across_runs(monkeypatch, tmp_path):
    ws1 = tmp_path / "a"
    ws2 = tmp_path / "b"
    ws1.mkdir()
    ws2.mkdir()

    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    monkeypatch.setattr(router, "ask_provider", lambda p, pr, **kw: _ok(p))

    result1 = router.run_combo("t", "prompt", working_directory=str(ws1))
    result2 = router.run_combo("t", "prompt", working_directory=str(ws2))

    assert result1.exit_code == 0
    assert result2.exit_code == 0


def test_confirmed_error_with_workspace_runs_fallback_and_releases(monkeypatch, tmp_path):
    ws = str(tmp_path)
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    calls = []

    def fake_ask(provider_id, prompt, **kw):
        calls.append(provider_id)
        if provider_id == "p1":
            return _fail_with_execution(
                provider_id,
                _bind_meta(_exec_meta("FAILED", pgid=123), kw["execution_id"], kw["attempt_id"]),
            )
        return _ok_with_execution(provider_id, kw["execution_id"], kw["attempt_id"])

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    result = router.run_combo("t", "prompt", working_directory=ws)
    assert result.exit_code == 0
    assert calls == ["p1", "p2"]
    assert workspace_lease.current_holder(ws) is None


def test_confirmed_timeout_with_workspace_runs_fallback_and_releases(monkeypatch, tmp_path):
    ws = str(tmp_path)
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    calls = []

    def fake_ask(provider_id, prompt, **kw):
        calls.append(provider_id)
        if provider_id == "p1":
            return _timeout(
                provider_id,
                _bind_meta(_exec_meta("CANCELLED", pgid=123), kw["execution_id"], kw["attempt_id"]),
            )
        return _ok_with_execution(provider_id, kw["execution_id"], kw["attempt_id"])

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    result = router.run_combo("t", "prompt", working_directory=ws)
    assert result.exit_code == 0
    assert calls == ["p1", "p2"]
    assert workspace_lease.current_holder(ws) is None


def test_verify_false_verdict_transfers_lease_to_p2_and_releases(monkeypatch, tmp_path):
    ws = str(tmp_path)
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())

    verify_calls = []
    recorded_verdicts = []
    attempts = {}

    def fake_verify_report(task_prompt, report, **kwargs):
        executor_provider = kwargs.get("executor_provider")
        verify_calls.append(
            {
                "task_prompt": task_prompt,
                "report": report,
                "executor_provider": executor_provider,
                "working_directory": kwargs.get("working_directory"),
            }
        )
        return Verdict(
            verdadeiro=False if executor_provider == "p1" else True,
            confianca="alta",
            motivos=["relatorio inconsistente com o workspace"],
            verificador="deterministic",
            execution=_verifier_meta(kwargs["execution_id"], kwargs["attempt_id"]),
        )

    def fake_record_verdict(executor_provider, verdict, **kwargs):
        recorded_verdicts.append(
            {
                "executor_provider": executor_provider,
                "verdadeiro": verdict.verdadeiro,
                "verificador": verdict.verificador,
                "project": kwargs.get("project"),
            }
        )

    def fake_ask(provider_id, prompt, **kw):
        holder = workspace_lease.current_holder(ws)
        assert holder is not None
        assert holder[0] == kw["execution_id"]
        assert holder[1] == kw["attempt_id"]

        if provider_id == "p1":
            attempts["p1"] = kw["attempt_id"]
            return _ok_with_execution(provider_id, kw["execution_id"], kw["attempt_id"])

        attempts["p2"] = kw["attempt_id"]
        assert attempts["p2"] != attempts["p1"]
        with pytest.raises(workspace_lease.WorkspaceLeaseError):
            workspace_lease.acquire(ws, "concurrent-exec", "attempt-x")
        return _ok_with_execution(provider_id, kw["execution_id"], kw["attempt_id"])

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    monkeypatch.setattr("athena.verifier.verify_report", fake_verify_report)
    monkeypatch.setattr("athena.reliability.record_verdict", fake_record_verdict)

    result = router.run_combo("t", "prompt", verify=True, working_directory=ws)

    assert result.exit_code == 0
    assert result.provider == "p2"
    assert verify_calls == [
        {
            "task_prompt": "prompt",
            "report": "ok",
            "executor_provider": "p1",
            "working_directory": ws,
        },
        {
            "task_prompt": "prompt",
            "report": "ok",
            "executor_provider": "p2",
            "working_directory": ws,
        },
    ]
    assert recorded_verdicts == [
        {
            "executor_provider": "p1",
            "verdadeiro": False,
            "verificador": "deterministic",
            "project": ws,
        },
        {
            "executor_provider": "p2",
            "verdadeiro": True,
            "verificador": "deterministic",
            "project": ws,
        },
    ]
    assert workspace_lease.current_holder(ws) is None


def test_verify_typeerror_propagates_once_and_retains_workspace_lease(monkeypatch, tmp_path):
    ws = str(tmp_path)
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    calls = {"count": 0}

    def fake_ask(provider_id, prompt, **kw):
        calls["count"] += 1
        return _ok_with_execution(provider_id, kw["execution_id"], kw["attempt_id"])

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    monkeypatch.setattr("athena.verifier.verify_report", lambda *_a, **_k: (_ for _ in ()).throw(TypeError("boom")))

    with pytest.raises(TypeError):
        router.run_combo("t", "prompt", verify=True, working_directory=ws)

    assert calls["count"] == 1
    assert workspace_lease.current_holder(ws) is not None


def test_verify_without_execution_blocks_with_workspace_and_retains_lease(monkeypatch, tmp_path):
    ws = str(tmp_path)
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    monkeypatch.setattr(
        router,
        "ask_provider",
        lambda provider_id, prompt, **kw: _ok_with_execution(provider_id, kw["execution_id"], kw["attempt_id"]),
    )
    monkeypatch.setattr(
        "athena.verifier.verify_report",
        lambda *_a, **_k: Verdict(verdadeiro=True, verificador="deterministic"),
    )

    with pytest.raises(router.FallbackBlocked):
        router.run_combo("t", "prompt", verify=True, working_directory=ws)
    assert workspace_lease.current_holder(ws) is not None


def test_verify_without_execution_keeps_compat_without_workspace(monkeypatch):
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    monkeypatch.setattr(router, "ask_provider", lambda provider_id, prompt, **kw: _ok(provider_id))
    monkeypatch.setattr(
        "athena.verifier.verify_report",
        lambda *_a, **_k: Verdict(verdadeiro=True, verificador="deterministic"),
    )

    result = router.run_combo("t", "prompt", verify=True)
    assert result.exit_code == 0
    assert result.execution is None


def test_other_execution_cannot_acquire_during_attempt_transfer(monkeypatch, tmp_path):
    ws = str(tmp_path)
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    blocked = {"value": False}

    def fake_ask(provider_id, prompt, **kw):
        if provider_id == "p1":
            with pytest.raises(workspace_lease.WorkspaceLeaseError):
                workspace_lease.acquire(ws, "concurrent-exec", "attempt-1")
            blocked["value"] = True
            return _fail_with_execution(
                provider_id,
                _bind_meta(_exec_meta("FAILED", pgid=123), kw["execution_id"], kw["attempt_id"]),
            )
        return _ok_with_execution(provider_id, kw["execution_id"], kw["attempt_id"])

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    result = router.run_combo("t", "prompt", working_directory=ws)
    assert result.exit_code == 0
    assert blocked["value"] is True
    assert workspace_lease.current_holder(ws) is None


def test_completed_with_unconfirmed_tree_blocks_and_retains_lease(monkeypatch, tmp_path):
    ws = str(tmp_path)
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())

    def fake_ask(provider_id, prompt, **kw):
        return _fail_with_execution(
            provider_id,
            _bind_meta(
                _exec_meta("COMPLETED", direct_confirmed=True, tree_confirmed=False, pgid=123),
                kw["execution_id"],
                kw["attempt_id"],
            ),
        )

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    with pytest.raises(router.FallbackBlocked):
        router.run_combo("t", "prompt", working_directory=ws)
    assert workspace_lease.current_holder(ws) is not None


def test_completed_confirmed_releases_when_policy_returns_result(monkeypatch, tmp_path):
    ws = str(tmp_path)
    combo = Combo(
        id="t",
        name="t",
        chain=[ComboStep(provider_id="p1"), ComboStep(provider_id="p2")],
        failover_policy=FailoverPolicy(on_error=False),
    )
    monkeypatch.setattr(router, "get_combo", lambda cid: combo)

    def fake_ask(provider_id, prompt, **kw):
        return _fail_with_execution(
            provider_id,
            _bind_meta(
                _exec_meta("COMPLETED", direct_confirmed=True, tree_confirmed=True, pgid=123),
                kw["execution_id"],
                kw["attempt_id"],
            ),
        )

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    result = router.run_combo("t", "prompt", working_directory=ws)
    assert result.exit_code == 1
    assert workspace_lease.current_holder(ws) is None


def test_missing_execution_metadata_with_workspace_is_fail_closed(monkeypatch, tmp_path):
    ws = str(tmp_path)
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    monkeypatch.setattr(router, "ask_provider", lambda p, pr, **kw: _fail(p))
    with pytest.raises(router.FallbackBlocked):
        router.run_combo("t", "prompt", working_directory=ws)
    assert workspace_lease.current_holder(ws) is not None


def test_real_pre_popen_failure_with_workspace_allows_fallback(monkeypatch, tmp_path):
    ws = str(tmp_path)
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    calls = []

    def fake_ask(provider_id, prompt, **kw):
        calls.append(provider_id)
        if provider_id == "p1":
            return run_subprocess(
                provider_id,
                ["athena-mcp-nonexistent-command-xyz"],
                timeout=5,
                execution_id=kw.get("execution_id"),
                attempt_id=kw.get("attempt_id"),
            )
        return _ok_with_execution(provider_id, kw["execution_id"], kw["attempt_id"])

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    result = router.run_combo("t", "prompt", working_directory=ws)
    assert result.exit_code == 0
    assert calls == ["p1", "p2"]
    assert workspace_lease.current_holder(ws) is None


def test_router_real_ask_provider_resolve_none_fallback_and_releases(monkeypatch, tmp_path):
    ws = str(tmp_path)
    combo = Combo(
        id="t",
        name="t",
        chain=[
            ComboStep(provider_id="p1"),
            ComboStep(
                provider_id="p2",
                extra_args=["-c", "import sys; print('ok-from-p2')"],
            ),
        ],
        failover_policy=FailoverPolicy(),
    )
    monkeypatch.setattr(router, "get_combo", lambda cid: combo)
    monkeypatch.setattr(router, "ask_provider", providers_module.ask_provider)

    p1 = ProviderSpec(
        id="p1",
        name="Synthetic Missing CLI",
        binary="p1-binary",
        description="Synthetic provider",
    )
    p2 = ProviderSpec(
        id="p2",
        name="Synthetic Python Success",
        binary=sys.executable,
        description="Synthetic provider",
    )
    monkeypatch.setitem(providers_module.PROVIDERS, "p1", p1)
    monkeypatch.setitem(providers_module.PROVIDERS, "p2", p2)

    original_resolve_binary = providers_module.resolve_binary

    def fake_resolve_binary(spec):
        if spec.id == "p1":
            return None
        if spec.id == "p2":
            return sys.executable
        return original_resolve_binary(spec)

    monkeypatch.setattr(providers_module, "resolve_binary", fake_resolve_binary)

    result = router.run_combo(
        "t",
        "ignored-prompt",
        working_directory=ws,
        timeout=5,
    )

    assert result.exit_code == 0
    assert result.execution is not None
    assert result.execution["state"] == "COMPLETED"
    assert workspace_lease.current_holder(ws) is None


def test_combo_exhausted_with_safe_termination_releases(monkeypatch, tmp_path):
    ws = str(tmp_path)
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())

    def fake_ask(provider_id, prompt, **kw):
        return _fail_with_execution(
            provider_id,
            _bind_meta(_exec_meta("FAILED", pgid=123), kw["execution_id"], kw["attempt_id"]),
        )

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    with pytest.raises(router.AllProvidersFailed):
        router.run_combo("t", "prompt", working_directory=ws)
    assert workspace_lease.current_holder(ws) is None


def test_indeterminate_exception_retains_lease(monkeypatch, tmp_path):
    ws = str(tmp_path)
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())

    class Boom(Exception):
        pass

    def fake_ask(provider_id, prompt, **kw):
        raise Boom("unexpected")

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    with pytest.raises(Boom):
        router.run_combo("t", "prompt", working_directory=ws)
    assert workspace_lease.current_holder(ws) is not None


def test_metadata_from_other_attempt_is_rejected(monkeypatch, tmp_path):
    ws = str(tmp_path)
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())

    def fake_ask(provider_id, prompt, **kw):
        if provider_id == "p1":
            return _fail_with_execution(
                provider_id,
                _bind_meta(_exec_meta("FAILED"), kw["execution_id"], "wrong-attempt-id"),
            )
        return _ok(provider_id)

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    with pytest.raises(router.FallbackBlocked):
        router.run_combo("t", "prompt", working_directory=ws)
    assert workspace_lease.current_holder(ws) is not None


def test_success_completed_with_unconfirmed_tree_retains_lease(monkeypatch, tmp_path):
    ws = str(tmp_path)
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())

    def fake_ask(provider_id, prompt, **kw):
        return RunResult(
            provider=provider_id,
            command=[],
            output="ok",
            exit_code=0,
            execution=_bind_meta(
                _exec_meta("COMPLETED", direct_confirmed=True, tree_confirmed=False, pgid=123),
                kw["execution_id"],
                kw["attempt_id"],
            ),
        )

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    result = router.run_combo("t", "prompt", working_directory=ws)
    assert result.exit_code == 0
    assert result.execution is not None
    assert result.execution["state"] == "COMPLETED"
    assert workspace_lease.current_holder(ws) is not None


def test_success_completed_remote_without_confirmation_retains_lease(monkeypatch, tmp_path):
    ws = str(tmp_path)
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())

    def fake_ask(provider_id, prompt, **kw):
        return RunResult(
            provider=provider_id,
            command=[],
            output="ok",
            exit_code=0,
            execution=_bind_meta(
                _exec_meta("COMPLETED", direct_confirmed=True, tree_confirmed=True, pgid=123)
                | {
                    "transport": "ssh",
                    "remote_session_started": True,
                    "remote_termination_confirmed": False,
                },
                kw["execution_id"],
                kw["attempt_id"],
            ),
        )

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    result = router.run_combo("t", "prompt", working_directory=ws)
    assert result.exit_code == 0
    assert workspace_lease.current_holder(ws) is not None


def test_run_combo_uses_execution_id_provided_by_caller(monkeypatch):
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    observed: dict[str, str] = {}

    def fake_ask(provider_id, prompt, **kw):
        observed["execution_id"] = kw["execution_id"]
        return _ok_with_execution(provider_id, kw["execution_id"], kw["attempt_id"])

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    result = router.run_combo("t", "prompt", execution_id="exec-from-mcp")
    assert result.exit_code == 0
    assert observed["execution_id"] == "exec-from-mcp"


def test_explicit_cancelled_without_timeout_does_not_start_fallback(monkeypatch):
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    calls = []

    def fake_ask(provider_id, prompt, **kw):
        calls.append(provider_id)
        return RunResult(
            provider=provider_id,
            command=[],
            output="",
            exit_code=130,
            timed_out=False,
            error="user_requested",
            execution=_bind_meta(
                _exec_meta("CANCELLED", direct_confirmed=True, tree_confirmed=True, pgid=123),
                kw["execution_id"],
                kw["attempt_id"],
            ),
        )

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    result = router.run_combo("t", "prompt")
    assert result.exit_code == 130
    assert calls == ["p1"]


def test_overall_timeout_prevents_starting_next_provider(monkeypatch):
    import time

    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    calls = []

    def fake_ask(provider_id, prompt, **kw):
        calls.append(provider_id)
        if provider_id == "p1":
            time.sleep(0.02)
            return _fail_with_execution(
                provider_id,
                _bind_meta(_exec_meta("FAILED", pgid=123), kw["execution_id"], kw["attempt_id"]),
            )
        return _ok(provider_id)

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    with pytest.raises(router.ComboDeadlineExceeded):
        router.run_combo("t", "prompt", overall_timeout=0.01)
    assert calls == ["p1"]


def test_overall_timeout_keeps_lease_when_last_metadata_unsafe(monkeypatch, tmp_path):
    ws = str(tmp_path)
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())

    def fake_ask(provider_id, prompt, **kw):
        return _fail_with_execution(
            provider_id,
            _bind_meta(
                _exec_meta("TERMINATION_UNCONFIRMED", direct_confirmed=True, tree_confirmed=False, pgid=123),
                kw["execution_id"],
                kw["attempt_id"],
            ),
        )

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    with pytest.raises(router.FallbackBlocked):
        router.run_combo("t", "prompt", working_directory=ws, overall_timeout=1.0)
    assert workspace_lease.current_holder(ws) is not None


def test_overall_timeout_releases_lease_when_last_metadata_safe(monkeypatch, tmp_path):
    import time

    ws = str(tmp_path)
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    calls = {"count": 0}

    def fake_ask(provider_id, prompt, **kw):
        calls["count"] += 1
        time.sleep(0.02)
        return _fail_with_execution(
            provider_id,
            _bind_meta(_exec_meta("FAILED", pgid=123), kw["execution_id"], kw["attempt_id"]),
        )

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    with pytest.raises(router.ComboDeadlineExceeded):
        router.run_combo("t", "prompt", working_directory=ws, overall_timeout=0.01)
    assert calls["count"] == 1
    assert workspace_lease.current_holder(ws) is None


def test_overall_timeout_after_attempt_started_with_untrusted_meta_blocks_and_retains_lease(
    monkeypatch, tmp_path
):
    import time

    ws = str(tmp_path)
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    calls = {"count": 0}

    def fake_ask(provider_id, prompt, **kw):
        calls["count"] += 1
        time.sleep(0.02)
        return _timeout(
            provider_id,
            _bind_meta(
                _exec_meta("TERMINATION_UNCONFIRMED", direct_confirmed=True, tree_confirmed=False, pgid=123),
                kw["execution_id"],
                kw["attempt_id"],
            ),
        )

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    with pytest.raises(router.FallbackBlocked):
        router.run_combo("t", "prompt", working_directory=ws, overall_timeout=0.01)
    assert calls["count"] == 1
    assert workspace_lease.current_holder(ws) is not None


def test_overall_timeout_after_transfer_before_verifier_start_releases_with_logical_timeout(
    monkeypatch, tmp_path
):
    import time

    ws = str(tmp_path)
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())

    def fake_ask(provider_id, prompt, **kw):
        return _ok_with_execution(provider_id, kw["execution_id"], kw["attempt_id"])

    def fake_verify_report(*_args, **_kwargs):
        raise AssertionError("verify_report não deveria iniciar quando o overall_timeout já expirou")

    real_transfer = workspace_lease.transfer

    def delayed_transfer(*args, **kwargs):
        time.sleep(0.02)
        return real_transfer(*args, **kwargs)

    monkeypatch.setattr(workspace_lease, "transfer", delayed_transfer)
    monkeypatch.setattr(router, "ask_provider", fake_ask)
    monkeypatch.setattr("athena.verifier.verify_report", fake_verify_report)

    with pytest.raises(router.ComboDeadlineExceeded):
        router.run_combo(
            "t",
            "prompt",
            verify=True,
            working_directory=ws,
            overall_timeout=0.01,
        )
    assert workspace_lease.current_holder(ws) is None


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_run_combo_rejects_non_finite_overall_timeout(monkeypatch, value):
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    with pytest.raises(ValueError):
        router.run_combo("t", "prompt", overall_timeout=value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_run_combo_rejects_non_finite_verification_timeout(monkeypatch, value):
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    with pytest.raises(ValueError):
        router.run_combo("t", "prompt", verification_timeout=value)


def test_invalid_explicit_timeout_with_workspace_fails_prelaunch_without_lease(monkeypatch):
    ws = "/tmp"
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    calls = {"count": 0}

    def fake_ask(*_args, **_kwargs):
        calls["count"] += 1
        return _ok("p1")

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    with pytest.raises(ValueError):
        router.run_combo("t", "prompt", working_directory=ws, timeout=0)
    assert calls["count"] == 0
    assert workspace_lease.current_holder(ws) is None


def test_later_step_invalid_timeout_fails_before_first_provider_and_without_lease(monkeypatch):
    ws = "/tmp"
    combo = Combo(
        id="t",
        name="t",
        chain=[
            ComboStep(provider_id="p1", timeout=30),
            ComboStep(provider_id="p2", timeout=0),
        ],
        failover_policy=FailoverPolicy(),
    )
    monkeypatch.setattr(router, "get_combo", lambda cid: combo)
    calls = {"count": 0}

    def fake_ask(*_args, **_kwargs):
        calls["count"] += 1
        return _ok("p1")

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    with pytest.raises(ValueError):
        router.run_combo("t", "prompt", working_directory=ws)
    assert calls["count"] == 0
    assert workspace_lease.current_holder(ws) is None


def test_verification_timeout_above_profile_max_fails_prelaunch_without_provider_or_lease(monkeypatch):
    ws = "/tmp"
    monkeypatch.setattr(router, "get_combo", lambda cid: _combo())
    calls = {"count": 0}

    def fake_ask(*_args, **_kwargs):
        calls["count"] += 1
        return _ok("p1")

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    with pytest.raises(ValueError):
        router.run_combo("t", "prompt", verify=True, working_directory=ws, verification_timeout=601)
    assert calls["count"] == 0
    assert workspace_lease.current_holder(ws) is None
