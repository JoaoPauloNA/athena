from __future__ import annotations

import pytest

import athena.providers as providers_module
from athena import mcp_server, router, verifier
from athena.bridge import RunResult, run_subprocess
from athena.combos import Combo, ComboStep, FailoverPolicy
from athena.execution_registry import ExecutionRegistry
from athena.providers import ProviderSpec, ask_provider, ask_provider_verified
from athena.service_profiles import (
    SERVICE_PROFILES,
    resolve_service_profile,
    resolve_timeouts,
)


def test_all_profiles_have_expected_shape():
    expected_max = {
        "text_generation": 600.0,
        "code_agent": 1800.0,
        "build_test": 1200.0,
        "research": 1800.0,
        "local_model": 900.0,
        "verification": 600.0,
        "workspace_mutation": 1800.0,
        "authenticated_external": 900.0,
        "unknown": 300.0,
    }
    assert set(SERVICE_PROFILES.keys()) == set(expected_max.keys())
    for profile_id, profile in SERVICE_PROFILES.items():
        assert profile.id == profile_id
        assert profile.default_idle_timeout_s is None
        assert profile.max_absolute_timeout_s == expected_max[profile_id]


def test_profile_inference_is_deterministic():
    assert resolve_service_profile(
        explicit_profile_id="unknown",
        provider_id="codex",
        task_type="frontend",
        working_directory="/tmp",
    ).id == "unknown"
    assert resolve_service_profile(
        explicit_profile_id=None,
        provider_id="ollama",
        task_type="frontend",
        working_directory="/tmp",
    ).id == "local_model"
    assert resolve_service_profile(
        explicit_profile_id=None,
        provider_id="codex",
        task_type="backend",
        working_directory=None,
    ).id == "code_agent"
    assert resolve_service_profile(
        explicit_profile_id=None,
        provider_id="codex",
        task_type="raciocinio",
        working_directory=None,
    ).id == "research"
    assert resolve_service_profile(
        explicit_profile_id=None,
        provider_id="codex",
        task_type="rapidez",
        working_directory=None,
    ).id == "text_generation"
    assert resolve_service_profile(
        explicit_profile_id=None,
        provider_id="codex",
        task_type=None,
        working_directory="/tmp",
    ).id == "code_agent"
    assert resolve_service_profile(
        explicit_profile_id=None,
        provider_id="codex",
        task_type=None,
        working_directory=None,
    ).id == "text_generation"
    with pytest.raises(ValueError):
        resolve_service_profile(
            explicit_profile_id="invalid",
            provider_id="codex",
            task_type=None,
            working_directory=None,
        )


def test_timeout_policy_rejects_max_excess_and_never_increases_provider_default():
    profile = SERVICE_PROFILES["code_agent"]
    with pytest.raises(ValueError):
        resolve_timeouts(
            profile=profile,
            provider_default_absolute_timeout_s=300,
            explicit_absolute_timeout_s=1801,
            explicit_idle_timeout_s=None,
        )
    absolute, idle = resolve_timeouts(
        profile=profile,
        provider_default_absolute_timeout_s=120,
        explicit_absolute_timeout_s=None,
        explicit_idle_timeout_s=None,
    )
    assert absolute == 120
    assert idle is None


def test_idle_timeout_is_none_by_default_and_validates_explicit():
    profile = SERVICE_PROFILES["text_generation"]
    absolute, idle = resolve_timeouts(
        profile=profile,
        provider_default_absolute_timeout_s=300,
        explicit_absolute_timeout_s=None,
        explicit_idle_timeout_s=60,
    )
    assert absolute == 300
    assert idle == 60
    with pytest.raises(ValueError):
        resolve_timeouts(
            profile=profile,
            provider_default_absolute_timeout_s=300,
            explicit_absolute_timeout_s=50,
            explicit_idle_timeout_s=60,
        )


def test_execution_metadata_includes_profile():
    result = run_subprocess(
        "synthetic-profile",
        ["python3", "-c", "print('ok')"],
        timeout=3,
        service_profile="verification",
    )
    assert result.execution is not None
    assert result.execution["profile"] == "verification"


def test_router_disables_fallback_for_authenticated_and_unknown(monkeypatch):
    combo = Combo(
        id="t",
        name="t",
        chain=[ComboStep(provider_id="p1"), ComboStep(provider_id="p2")],
        failover_policy=FailoverPolicy(on_error=True),
    )
    monkeypatch.setattr(router, "get_combo", lambda _cid: combo)
    calls: list[str] = []

    def fake_ask(provider_id, _prompt, **_kwargs):
        calls.append(provider_id)
        return RunResult(provider=provider_id, command=[], output="", exit_code=1, error="boom")

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    result = router.run_combo("t", "prompt", service_profile="authenticated_external")
    assert result.exit_code == 1
    assert calls == ["p1"]

    calls.clear()
    result = router.run_combo("t", "prompt", service_profile="unknown")
    assert result.exit_code == 1
    assert calls == ["p1"]


def test_workspace_required_profile_blocks_prelaunch(monkeypatch):
    combo = Combo(
        id="t",
        name="t",
        chain=[ComboStep(provider_id="p1")],
        failover_policy=FailoverPolicy(),
    )
    monkeypatch.setattr(router, "get_combo", lambda _cid: combo)
    called = {"value": False}

    def fake_ask(*_args, **_kwargs):
        called["value"] = True
        return RunResult(provider="p1", command=[], output="", exit_code=0)

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    with pytest.raises(ValueError):
        router.run_combo("t", "prompt", task_type="frontend")
    assert called["value"] is False


def test_direct_and_combo_path_accept_service_profile(monkeypatch, tmp_path):
    provider = ProviderSpec(
        id="synthetic",
        name="Synthetic",
        binary="python3",
        description="synthetic",
    )
    monkeypatch.setitem(providers_module.PROVIDERS, "synthetic", provider)
    result = ask_provider(
        "synthetic",
        "ignored",
        working_directory=str(tmp_path),
        extra_args=["-c", "print('ok')"],
        use_default_role=False,
        service_profile="code_agent",
    )
    assert result.execution is not None
    assert result.execution["profile"] == "code_agent"


def test_mcp_timeout_over_profile_max_is_failed_prelaunch():
    mcp_server.EXECUTION_REGISTRY = ExecutionRegistry()
    params = {
        "name": "ask_provider",
        "arguments": {
            "provider": "codex",
            "prompt": "x",
            "service_profile": "unknown",
            "timeout": 301,
        },
    }
    execution_id = mcp_server._register_execution_if_needed("req-profile-max", params)
    response = mcp_server._handle_request(
        {
            "jsonrpc": "2.0",
            "id": "req-profile-max",
            "method": "tools/call",
            "params": params,
        }
    )
    assert response is not None
    assert response["error"]["code"] == -32602
    entry = mcp_server.EXECUTION_REGISTRY.get(execution_id=execution_id)
    assert entry is not None
    assert entry["state"] == "FAILED_PRELAUNCH"


def test_verifier_children_use_verification_profile(monkeypatch, tmp_path):
    observed: list[str | None] = []

    def fake_run(_provider, _argv, **kwargs):
        observed.append(kwargs.get("service_profile"))
        return RunResult(
            provider="verifier",
            command=[],
            output="",
            exit_code=0,
            stdout=str(tmp_path),
            stderr="",
            execution={"state": "COMPLETED", "profile": kwargs.get("service_profile")},
        )

    monkeypatch.setattr(verifier, "run_subprocess", fake_run)
    verifier.collect_evidence(str(tmp_path), "report")
    assert observed
    assert all(item == "verification" for item in observed)


def test_mcp_run_combo_uses_primary_provider_for_profile_inference(monkeypatch):
    combo = Combo(
        id="default",
        name="default",
        chain=[ComboStep(provider_id="ollama"), ComboStep(provider_id="codex")],
        failover_policy=FailoverPolicy(),
    )
    monkeypatch.setattr(mcp_server, "get_combo", lambda _cid: combo)
    captured: dict[str, object] = {}

    def fake_run_combo(*args, **kwargs):
        captured["service_profile"] = kwargs.get("service_profile")
        return RunResult(provider="ollama", command=[], output="ok", exit_code=0)

    monkeypatch.setattr(mcp_server, "run_combo", fake_run_combo)
    payload = mcp_server._handle_run_combo(
        {
            "combo_id": "default",
            "prompt": "x",
            "task_type": "frontend",
        }
    )
    assert payload is not None
    assert captured["service_profile"] == "local_model"


def test_router_profile_inference_remains_stable_across_fallback(monkeypatch):
    combo = Combo(
        id="t",
        name="t",
        chain=[ComboStep(provider_id="ollama"), ComboStep(provider_id="codex")],
        failover_policy=FailoverPolicy(on_error=True),
    )
    monkeypatch.setattr(router, "get_combo", lambda _cid: combo)
    seen_profiles: list[str | None] = []

    def fake_ask(provider_id, _prompt, **kwargs):
        seen_profiles.append(kwargs.get("service_profile"))
        if provider_id == "ollama":
            return RunResult(provider=provider_id, command=[], output="", exit_code=1, error="boom")
        return RunResult(provider=provider_id, command=[], output="ok", exit_code=0)

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    result = router.run_combo("t", "prompt", task_type="frontend")
    assert result.exit_code == 0
    assert seen_profiles == ["local_model", "local_model"]


@pytest.mark.parametrize("bad_timeout", [0, True, float("nan"), float("inf"), float("-inf")])
def test_run_combo_rejects_invalid_explicit_timeout_before_provider_call(monkeypatch, bad_timeout):
    combo = Combo(
        id="t",
        name="t",
        chain=[ComboStep(provider_id="codex")],
        failover_policy=FailoverPolicy(),
    )
    monkeypatch.setattr(router, "get_combo", lambda _cid: combo)
    called = {"value": False}

    def fake_ask(*_args, **_kwargs):
        called["value"] = True
        return RunResult(provider="codex", command=[], output="ok", exit_code=0)

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    with pytest.raises(ValueError):
        router.run_combo("t", "prompt", timeout=bad_timeout)
    assert called["value"] is False


def test_run_combo_accepts_valid_explicit_timeout_and_forwards_effective_value(monkeypatch):
    combo = Combo(
        id="t",
        name="t",
        chain=[ComboStep(provider_id="codex", timeout=42)],
        failover_policy=FailoverPolicy(),
    )
    monkeypatch.setattr(router, "get_combo", lambda _cid: combo)
    observed_timeout: dict[str, float | None] = {"value": None}

    def fake_ask(_provider_id, _prompt, **kwargs):
        observed_timeout["value"] = kwargs.get("timeout")
        return RunResult(provider="codex", command=[], output="ok", exit_code=0)

    monkeypatch.setattr(router, "ask_provider", fake_ask)
    result = router.run_combo("t", "prompt", timeout=17)
    assert result.exit_code == 0
    assert observed_timeout["value"] == 17


def test_ask_provider_known_provider_validates_timeout_before_binary_lookup(monkeypatch):
    provider = ProviderSpec(
        id="synthetic-known-timeout",
        name="Synthetic Known Timeout",
        binary="synthetic-known-timeout-bin",
        description="synthetic",
        default_timeout=300,
    )
    monkeypatch.setitem(providers_module.PROVIDERS, provider.id, provider)
    monkeypatch.setattr(providers_module, "resolve_binary", lambda _spec: None)

    with pytest.raises(ValueError):
        ask_provider(
            provider.id,
            "prompt",
            timeout=0,
            service_profile="unknown",
        )


@pytest.mark.parametrize("profile_id", ["authenticated_external", "unknown"])
def test_ask_provider_verified_blocks_auto_retry_when_profile_forbids_fallback(monkeypatch, profile_id):
    executor_calls = {"count": 0}
    record_calls = {"count": 0}

    def fake_ask_provider(*_args, **_kwargs):
        executor_calls["count"] += 1
        return RunResult(
            provider="codex",
            command=[],
            output="report",
            exit_code=0,
            execution={
                "execution_id": _kwargs["execution_id"],
                "attempt_id": _kwargs["attempt_id"],
                "state": "COMPLETED",
                "process_created": True,
                "direct_process_terminated_confirmed": True,
                "process_tree_terminated_confirmed": True,
                "pgid": 123,
            },
        )

    def fake_verify_report(*_args, **_kwargs):
        return verifier.Verdict(
            verdadeiro=False,
            confianca="alta",
            motivos=["evidence mismatch"],
            verificador="deterministic",
            execution={
                "execution_id": _kwargs["execution_id"],
                "attempt_id": _kwargs["attempt_id"],
                "state": "COMPLETED",
                "process_created": False,
                "direct_process_terminated_confirmed": True,
                "process_tree_terminated_confirmed": True,
                "pgid": None,
            },
        )

    def fake_record_verdict(*_args, **_kwargs):
        record_calls["count"] += 1

    monkeypatch.setattr(providers_module, "ask_provider", fake_ask_provider)
    monkeypatch.setattr("athena.verifier.verify_report", fake_verify_report)
    monkeypatch.setattr("athena.reliability.record_verdict", fake_record_verdict)
    monkeypatch.setattr("athena.verifier.MAX_FIX_ATTEMPTS", 2)

    result = ask_provider_verified(
        "codex",
        "task",
        service_profile=profile_id,
    )
    assert executor_calls["count"] == 1
    assert record_calls["count"] == 1
    assert result.verdict is not None
    assert result.verdict.get("escalado") is True
    assert any("Retry corretivo automático bloqueado" in warning for warning in result.warnings)


def test_ask_provider_verified_keeps_retry_when_profile_allows_fallback(monkeypatch):
    executor_calls = {"count": 0}
    verify_calls = {"count": 0}

    def fake_ask_provider(*_args, **_kwargs):
        executor_calls["count"] += 1
        return RunResult(
            provider="codex",
            command=[],
            output=f"report-{executor_calls['count']}",
            exit_code=0,
            execution={
                "execution_id": _kwargs["execution_id"],
                "attempt_id": _kwargs["attempt_id"],
                "state": "COMPLETED",
                "process_created": True,
                "direct_process_terminated_confirmed": True,
                "process_tree_terminated_confirmed": True,
                "pgid": 123,
            },
        )

    def fake_verify_report(*_args, **_kwargs):
        verify_calls["count"] += 1
        if verify_calls["count"] == 1:
            return verifier.Verdict(
                verdadeiro=False,
                confianca="alta",
                motivos=["first false"],
                verificador="deterministic",
                execution={
                    "execution_id": _kwargs["execution_id"],
                    "attempt_id": _kwargs["attempt_id"],
                    "state": "COMPLETED",
                    "process_created": False,
                    "direct_process_terminated_confirmed": True,
                    "process_tree_terminated_confirmed": True,
                    "pgid": None,
                },
            )
        return verifier.Verdict(
            verdadeiro=True,
            confianca="alta",
            motivos=["fixed"],
            verificador="deterministic",
            execution={
                "execution_id": _kwargs["execution_id"],
                "attempt_id": _kwargs["attempt_id"],
                "state": "COMPLETED",
                "process_created": False,
                "direct_process_terminated_confirmed": True,
                "process_tree_terminated_confirmed": True,
                "pgid": None,
            },
        )

    monkeypatch.setattr(providers_module, "ask_provider", fake_ask_provider)
    monkeypatch.setattr("athena.verifier.verify_report", fake_verify_report)
    monkeypatch.setattr("athena.reliability.record_verdict", lambda *_a, **_k: None)
    monkeypatch.setattr("athena.verifier.MAX_FIX_ATTEMPTS", 2)

    result = ask_provider_verified(
        "codex",
        "task",
        service_profile="text_generation",
    )
    assert result.verdict is not None
    assert result.verdict.get("verdadeiro") is True
    assert executor_calls["count"] == 2
