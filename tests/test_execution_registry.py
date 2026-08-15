from __future__ import annotations

import json

import pytest

from athena.execution import ExecutionControl
from athena.execution_registry import ExecutionRegistry


def test_registry_create_update_lookup_by_execution_id_and_request_id():
    registry = ExecutionRegistry()
    registry.create(execution_id="exec-1", request_id="req-1", tool="run_combo")
    registry.update_attempt(
        "exec-1",
        {
            "execution_id": "exec-1",
            "attempt_id": "att-1",
            "state": "RUNNING",
        },
    )

    by_execution = registry.get(execution_id="exec-1")
    by_request = registry.get(request_id="req-1")
    assert by_execution is not None
    assert by_request is not None
    assert by_execution["execution_id"] == "exec-1"
    assert by_request["execution_id"] == "exec-1"
    assert by_execution["current_attempt_id"] == "att-1"


def test_registry_serialization_remains_sanitized():
    registry = ExecutionRegistry()
    registry.create(execution_id="exec-2", request_id="req-2", tool="ask_provider")
    registry.update_attempt(
        "exec-2",
        {
            "execution_id": "exec-2",
            "attempt_id": "att-2",
            "state": "FAILED",
            "prompt": "SECRET_PROMPT",
            "response": "SECRET_RESPONSE",
            "command": ["echo", "secret"],
            "api_key": "sk-secret",
            "nested": {"token": "SECRET_TOKEN", "safe": "ok"},
        },
    )

    payload = registry.get(execution_id="exec-2")
    assert payload is not None
    text = str(payload)
    for forbidden in ("SECRET_PROMPT", "SECRET_RESPONSE", "sk-secret", "SECRET_TOKEN", "echo"):
        assert forbidden not in text


def test_registry_updates_preserve_attempt_ids():
    registry = ExecutionRegistry()
    registry.create(execution_id="exec-3", request_id="req-3", tool="run_combo")
    registry.update_attempt(
        "exec-3",
        {"execution_id": "exec-3", "attempt_id": "att-a", "state": "RUNNING"},
    )
    registry.update_attempt(
        "exec-3",
        {"execution_id": "exec-3", "attempt_id": "att-b", "state": "FAILED"},
    )
    payload = registry.get(execution_id="exec-3")
    assert payload is not None
    assert payload["attempt_order"] == ["att-a", "att-b"]
    assert payload["attempts"]["att-a"]["attempt_id"] == "att-a"
    assert payload["attempts"]["att-b"]["attempt_id"] == "att-b"


def test_registry_rejects_duplicate_execution_and_request_ids_without_overwrite():
    registry = ExecutionRegistry()
    first = registry.create(execution_id="exec-dup", request_id="req-dup-1", tool="run_combo")
    with pytest.raises(ValueError, match="execution_id duplicado"):
        registry.create(execution_id="exec-dup", request_id="req-dup-2", tool="ask_provider")
    with pytest.raises(ValueError, match="request_id duplicado"):
        registry.create(execution_id="exec-dup-2", request_id="req-dup-1", tool="ask_provider")
    current = registry.get(execution_id="exec-dup")
    assert current == first
    assert registry.get(request_id="req-dup-2") is None


def test_registry_rejects_update_without_attempt_id():
    registry = ExecutionRegistry()
    registry.create(execution_id="exec-no-att", request_id="req-no-att", tool="run_combo")
    with pytest.raises(ValueError, match="attempt_id inválido"):
        registry.update_attempt("exec-no-att", {"execution_id": "exec-no-att", "state": "RUNNING"})


def test_registry_rejects_update_with_mismatched_execution_id():
    registry = ExecutionRegistry()
    registry.create(execution_id="exec-a", request_id="req-a", tool="run_combo")
    with pytest.raises(ValueError, match="execution_id divergente"):
        registry.update_attempt(
            "exec-a",
            {"execution_id": "exec-b", "attempt_id": "att-x", "state": "RUNNING"},
        )


def test_registry_return_payload_mutation_does_not_change_internal_state():
    registry = ExecutionRegistry()
    created = registry.create(execution_id="exec-copy", request_id="req-copy", tool="run_combo")
    created["state"] = "MUTATED"
    created["attempt_order"].append("fake-att")
    created["attempts"]["fake-att"] = {"attempt_id": "fake-att", "state": "RUNNING"}

    registry.update_attempt(
        "exec-copy",
        {
            "execution_id": "exec-copy",
            "attempt_id": "att-1",
            "state": "RUNNING",
            "nested": {
                "items": [{"Token": "hidden", "safe": "ok"}],
                "tuple_data": ({"Secret": "x"}, "y"),
                "set_data": {1, 2, 3},
            },
        },
    )
    payload = registry.get(execution_id="exec-copy")
    assert payload is not None
    payload["attempt_order"].append("fake-2")
    again = registry.get(execution_id="exec-copy")
    assert again is not None
    assert again["state"] != "MUTATED"
    assert "fake-att" not in again["attempt_order"]
    assert "fake-2" not in again["attempt_order"]
    assert "nested" not in again["attempts"]["att-1"]
    text = str(again)
    assert "hidden" not in text


def test_registry_sanitize_tuple_and_set_are_json_safe_and_secret_free():
    registry = ExecutionRegistry()
    registry.create(execution_id="exec-json-safe", request_id="req-json-safe", tool="run_combo")
    registry.update_attempt(
        "exec-json-safe",
        {
            "execution_id": "exec-json-safe",
            "attempt_id": "att-json-safe",
            "state": "RUNNING",
            "nested": {
                "tuple_data": ({"Secret": "hide-me", "safe": "ok"}, ["x", {"token": "hide-too"}]),
                "set_data": {
                    "z",
                    ("t", ("safe_key", "ok"), ("value", 1)),
                    ("n", ("k", "v")),
                },
            },
        },
    )
    payload = registry.get(execution_id="exec-json-safe")
    assert payload is not None
    encoded = json.dumps(payload)
    decoded = json.loads(encoded)

    attempt = decoded["attempts"]["att-json-safe"]
    assert "nested" not in attempt

    text = str(decoded)
    for forbidden in ("hide-me", "hide-too"):
        assert forbidden not in text


def test_mark_state_if_not_in_does_not_override_terminal_state():
    registry = ExecutionRegistry()
    registry.create(execution_id="exec-terminal", request_id="req-terminal", tool="ask_provider")
    registry.mark_state("exec-terminal", "COMPLETED")

    snapshot = registry.mark_state_if_not_in("exec-terminal", "RESULT_INDETERMINATE", {"COMPLETED", "FAILED"})
    assert snapshot is not None
    assert snapshot["state"] == "COMPLETED"
    current = registry.get(execution_id="exec-terminal")
    assert current is not None
    assert current["state"] == "COMPLETED"


def test_request_cancel_is_idempotent_and_supports_lookup_by_request_id():
    registry = ExecutionRegistry()
    control = ExecutionControl()
    registry.create(
        execution_id="exec-cancel",
        request_id="req-cancel",
        tool="run_combo",
        control=control,
    )

    first = registry.request_cancel(request_id="req-cancel", reason="free text from user")
    second = registry.request_cancel(execution_id="exec-cancel", reason="another free text")

    assert first["found"] is True
    assert first["requested"] is True
    assert first["execution_id"] == "exec-cancel"
    assert second["found"] is True
    assert second["requested"] is False
    assert control.cancellation_requested is True
    assert control.cancel_reason == "user_requested"


def test_update_attempt_keeps_client_abandoned_sticky_once_true():
    registry = ExecutionRegistry()
    registry.create(execution_id="exec-sticky", request_id="req-sticky", tool="run_combo")
    registry.update_attempt(
        "exec-sticky",
        {
            "execution_id": "exec-sticky",
            "attempt_id": "att-1",
            "state": "RUNNING",
            "client_abandoned": True,
        },
    )
    registry.update_attempt(
        "exec-sticky",
        {
            "execution_id": "exec-sticky",
            "attempt_id": "att-1",
            "state": "RUNNING",
            "client_abandoned": False,
        },
    )
    payload = registry.get(execution_id="exec-sticky")
    assert payload is not None
    assert payload["client_abandoned"] is True


def test_request_cancel_terminal_state_is_noop_for_control_and_timestamps():
    registry = ExecutionRegistry()
    control = ExecutionControl()
    registry.create(
        execution_id="exec-term-noop",
        request_id="req-term-noop",
        tool="run_combo",
        control=control,
    )
    registry.mark_state("exec-term-noop", "RESULT_INDETERMINATE")
    registry.finalize("exec-term-noop")
    before = registry.get(execution_id="exec-term-noop")
    assert before is not None

    result = registry.request_cancel(execution_id="exec-term-noop", reason="free text")
    after = registry.get(execution_id="exec-term-noop")
    assert after is not None

    assert result["found"] is True
    assert result["requested"] is False
    assert result["execution_id"] == "exec-term-noop"
    assert control.cancellation_requested is False
    assert after["updated_at_utc"] == before["updated_at_utc"]
    assert after["client_abandoned"] == before["client_abandoned"]


def test_registry_payload_never_serializes_internal_control():
    registry = ExecutionRegistry()
    registry.create(
        execution_id="exec-no-control",
        request_id="req-no-control",
        tool="run_combo",
        control=ExecutionControl(),
    )
    payload = registry.get(execution_id="exec-no-control")
    assert payload is not None
    assert "control" not in payload


def test_registry_capacity_evicts_oldest_finalized_and_cleans_request_mapping():
    registry = ExecutionRegistry(max_records=2)
    registry.create(execution_id="exec-1", request_id="req-1", tool="run_combo")
    registry.create(execution_id="exec-2", request_id="req-2", tool="run_combo")
    registry.finalize("exec-1", state="COMPLETED")

    registry.create(execution_id="exec-3", request_id="req-3", tool="run_combo")
    assert registry.get(execution_id="exec-1") is None
    assert registry.get(request_id="req-1") is None
    assert registry.get(execution_id="exec-2") is not None
    assert registry.get(execution_id="exec-3") is not None


def test_registry_capacity_rejects_create_when_all_records_active():
    registry = ExecutionRegistry(max_records=1)
    registry.create(execution_id="exec-1", request_id="req-1", tool="run_combo")
    with pytest.raises(ValueError, match="lotado com execuções ativas"):
        registry.create(execution_id="exec-2", request_id="req-2", tool="run_combo")


def test_registry_attempt_cap_discards_oldest_non_current():
    registry = ExecutionRegistry(max_attempts_per_record=2)
    registry.create(execution_id="exec-att-cap", request_id="req-att-cap", tool="run_combo")
    registry.update_attempt("exec-att-cap", {"execution_id": "exec-att-cap", "attempt_id": "a1", "state": "FAILED"})
    registry.update_attempt("exec-att-cap", {"execution_id": "exec-att-cap", "attempt_id": "a2", "state": "FAILED"})
    registry.update_attempt("exec-att-cap", {"execution_id": "exec-att-cap", "attempt_id": "a3", "state": "RUNNING"})
    payload = registry.get(execution_id="exec-att-cap")
    assert payload is not None
    assert payload["attempt_order"] == ["a2", "a3"]
    assert "a1" not in payload["attempts"]
    assert payload["attempts_truncated"] == 1


def test_finalize_ignores_late_update_and_keeps_snapshot_stable():
    registry = ExecutionRegistry()
    registry.create(execution_id="exec-finalized", request_id="req-finalized", tool="run_combo")
    registry.update_attempt(
        "exec-finalized",
        {"execution_id": "exec-finalized", "attempt_id": "att-1", "state": "RUNNING"},
    )
    registry.finalize("exec-finalized", state="FAILED")
    after_finalize = registry.get(execution_id="exec-finalized")
    assert after_finalize is not None
    registry.update_attempt(
        "exec-finalized",
        {"execution_id": "exec-finalized", "attempt_id": "att-2", "state": "COMPLETED"},
    )
    late = registry.get(execution_id="exec-finalized")
    assert late == after_finalize


def test_late_update_on_old_attempt_cannot_become_current_or_override_aggregate():
    registry = ExecutionRegistry()
    registry.create(execution_id="exec-old-attempt", request_id="req-old-attempt", tool="run_combo")
    registry.update_attempt(
        "exec-old-attempt",
        {"execution_id": "exec-old-attempt", "attempt_id": "att-1", "state": "FAILED"},
    )
    registry.update_attempt(
        "exec-old-attempt",
        {"execution_id": "exec-old-attempt", "attempt_id": "att-2", "state": "RUNNING"},
    )
    registry.update_attempt(
        "exec-old-attempt",
        {"execution_id": "exec-old-attempt", "attempt_id": "att-1", "state": "COMPLETED"},
    )
    payload = registry.get(execution_id="exec-old-attempt")
    assert payload is not None
    assert payload["current_attempt_id"] == "att-2"
    assert payload["state"] == "RUNNING"
    assert payload["attempts"]["att-1"]["state"] == "FAILED"


def test_terminal_same_attempt_cannot_regress():
    registry = ExecutionRegistry()
    registry.create(execution_id="exec-regress", request_id="req-regress", tool="run_combo")
    registry.update_attempt(
        "exec-regress",
        {"execution_id": "exec-regress", "attempt_id": "att-1", "state": "FAILED"},
    )
    registry.update_attempt(
        "exec-regress",
        {"execution_id": "exec-regress", "attempt_id": "att-1", "state": "RUNNING"},
    )
    payload = registry.get(execution_id="exec-regress")
    assert payload is not None
    assert payload["attempts"]["att-1"]["state"] == "FAILED"


def test_terminal_same_attempt_ignores_late_snapshot_integrally():
    registry = ExecutionRegistry()
    registry.create(execution_id="exec-integral", request_id="req-integral", tool="run_combo")
    registry.update_attempt(
        "exec-integral",
        {
            "execution_id": "exec-integral",
            "attempt_id": "att-1",
            "state": "FAILED",
            "direct_process_terminated_confirmed": True,
            "process_tree_terminated_confirmed": True,
            "history": [
                {
                    "from_state": "RUNNING",
                    "to_state": "FAILED",
                    "at_utc": "2026-01-01T00:00:00Z",
                    "at_monotonic": 1.0,
                    "reason": "verification_deadline",
                }
            ],
        },
    )
    before = registry.get(execution_id="exec-integral")
    assert before is not None
    registry.update_attempt(
        "exec-integral",
        {
            "execution_id": "exec-integral",
            "attempt_id": "att-1",
            "state": "RUNNING",
            "direct_process_terminated_confirmed": False,
            "process_tree_terminated_confirmed": False,
            "history": [
                {
                    "from_state": "FAILED",
                    "to_state": "RUNNING",
                    "at_utc": "/tmp/secret/path",
                    "at_monotonic": float("nan"),
                    "reason": "prompt leaked",
                }
            ],
        },
    )
    after = registry.get(execution_id="exec-integral")
    assert after == before


def test_cancel_uses_finalized_not_intermediate_failed_attempt():
    control = ExecutionControl()
    registry = ExecutionRegistry()
    registry.create(
        execution_id="exec-intermediate-failed",
        request_id="req-intermediate-failed",
        tool="run_combo",
        control=control,
    )
    registry.update_attempt(
        "exec-intermediate-failed",
        {"execution_id": "exec-intermediate-failed", "attempt_id": "att-1", "state": "FAILED"},
    )
    result = registry.request_cancel(execution_id="exec-intermediate-failed", reason="user free text")
    assert result["found"] is True
    assert result["requested"] is True


def test_registry_sanitization_drops_sensitive_injected_fields():
    registry = ExecutionRegistry()
    registry.create(execution_id="exec-sanitize", request_id="req-sanitize", tool="run_combo")
    registry.update_attempt(
        "exec-sanitize",
        {
            "execution_id": "exec-sanitize",
            "attempt_id": "att-1",
            "state": "FAILED",
            "termination_reason": "user_prompt leaked /tmp/path with password",
            "history": [{"from_state": "RUNNING", "to_state": "FAILED", "reason": "access_token leaked"}],
            "prompt": "secret prompt",
            "output": "secret output",
            "access_token": "tok-123",
            "password": "pw",
            "path": "/tmp/secret",
            "env": {"A": "b"},
            "args": ["x"],
        },
    )
    payload = registry.get(execution_id="exec-sanitize")
    assert payload is not None
    text = json.dumps(payload, ensure_ascii=False)
    for forbidden in ("secret prompt", "secret output", "tok-123", "password", "/tmp/secret", "access_token"):
        assert forbidden not in text
    attempt = payload["attempts"]["att-1"]
    assert attempt["termination_reason"] in {"internal_error", "other_redacted"}
    assert attempt["history"][0]["reason"] == "other_redacted"


def test_registry_strict_sanitization_inside_allowed_fields():
    registry = ExecutionRegistry()
    registry.create(execution_id="exec-strict", request_id="req-strict", tool="run_combo")
    registry.update_attempt(
        "exec-strict",
        {
            "execution_id": "exec-strict",
            "attempt_id": "att-1",
            "state": "FAILED",
            "is_terminal": False,
            "provider": "Codex ../../secret/path token",
            "profile": "FAST profile /tmp/password",
            "transport": "SSH!!!",
            "created_at_utc": "/Users/private/token",
            "state_entered_at_monotonic": float("inf"),
            "history": [
                {
                    "from_state": "RUNNING",
                    "to_state": "FAILED",
                    "at_utc": "/tmp/secret/path",
                    "at_monotonic": float("nan"),
                    "reason": "prompt token leaked",
                }
            ],
        },
    )
    payload = registry.get(execution_id="exec-strict")
    assert payload is not None
    attempt = payload["attempts"]["att-1"]
    assert attempt["is_terminal"] is True
    assert attempt["provider"] == "redacted_identifier"
    assert attempt["profile"] == "redacted_identifier"
    assert attempt["transport"] == "unknown"
    assert attempt["created_at_utc"] is None
    assert attempt["state_entered_at_monotonic"] is None
    assert attempt["history"][0]["from_state"] == "RUNNING"
    assert attempt["history"][0]["to_state"] == "FAILED"
    assert attempt["history"][0]["at_utc"] is None
    assert attempt["history"][0]["at_monotonic"] is None
    assert attempt["history"][0]["reason"] == "other_redacted"
    serialized = json.dumps(payload, ensure_ascii=False)
    for forbidden in ("/Users/private/token", "/tmp/secret/path", "prompt token leaked"):
        assert forbidden not in serialized


def test_registry_constructor_validates_positive_integer_limits():
    with pytest.raises(ValueError, match="max_records deve ser inteiro positivo"):
        ExecutionRegistry(max_records=True)
    with pytest.raises(ValueError, match="max_attempts_per_record deve ser inteiro positivo"):
        ExecutionRegistry(max_attempts_per_record=0)


def test_registry_create_duplicate_does_not_evict_with_full_capacity():
    registry = ExecutionRegistry(max_records=1)
    registry.create(execution_id="exec-1", request_id="req-1", tool="run_combo")
    with pytest.raises(ValueError, match="execution_id duplicado"):
        registry.create(execution_id="exec-1", request_id="req-2", tool="run_combo")
    assert registry.get(execution_id="exec-1") is not None
    assert registry.get(request_id="req-1") is not None


def test_registry_create_rejects_invalid_execution_id():
    registry = ExecutionRegistry()
    with pytest.raises(ValueError, match="execution_id inválido"):
        registry.create(execution_id="invalid id with space", request_id="req-ok", tool="run_combo")


def test_registry_public_request_id_masks_sensitive_strings_but_keeps_lookup():
    registry = ExecutionRegistry()
    sensitive = "TOKEN=sk-12345 /Users/joao/private/path"
    payload = registry.create(
        execution_id="exec-safe-req",
        request_id=sensitive,
        tool="run_combo",
    )
    assert payload["request_id"].startswith("sha256:")
    assert payload["public_request_id"] == payload["request_id"]
    assert sensitive not in str(payload)
    assert registry.get(request_id=sensitive) is not None


def test_registry_public_request_id_keeps_int_and_safe_string():
    registry = ExecutionRegistry()
    payload_int = registry.create(execution_id="exec-int-req", request_id=42, tool="run_combo")
    payload_str = registry.create(execution_id="exec-str-req", request_id="req-safe-1", tool="run_combo")
    assert payload_int["request_id"] == 42
    assert payload_str["request_id"] == "req-safe-1"


def test_registry_identifier_allows_short_model_style_slash_for_verifier_like_values():
    registry = ExecutionRegistry()
    registry.create(execution_id="exec-slash-1", request_id="req-slash-1", tool="run_combo")
    registry.update_attempt(
        "exec-slash-1",
        {
            "execution_id": "exec-slash-1",
            "attempt_id": "att-1",
            "state": "RUNNING",
            "provider": "opencode/x",
            "profile": "verifier",
            "transport": "ssh",
        },
    )
    attempt = registry.get(execution_id="exec-slash-1")["attempts"]["att-1"]
    assert attempt["provider"] == "opencode/x"
    assert attempt["profile"] == "verifier"
    assert attempt["transport"] == "ssh"


def test_registry_history_is_capped_per_snapshot():
    registry = ExecutionRegistry()
    registry.create(execution_id="exec-hist-cap", request_id="req-hist-cap", tool="run_combo")
    history = [
        {"from_state": "RUNNING", "to_state": "FAILED", "at_utc": "2026-01-01T00:00:00Z", "at_monotonic": float(i)}
        for i in range(64)
    ]
    registry.update_attempt(
        "exec-hist-cap",
        {
            "execution_id": "exec-hist-cap",
            "attempt_id": "att-hist",
            "state": "FAILED",
            "history": history,
        },
    )
    attempt = registry.get(execution_id="exec-hist-cap")["attempts"]["att-hist"]
    assert len(attempt["history"]) == 32


def test_mark_state_validates_state_names():
    registry = ExecutionRegistry()
    registry.create(execution_id="exec-state-validate", request_id="req-state-validate", tool="run_combo")
    with pytest.raises(ValueError, match="state inválido"):
        registry.mark_state("exec-state-validate", "BAD_STATE")
