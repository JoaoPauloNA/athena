"""Testes do registro modular e limitado de execuções."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from athena.execution import CancellationToken
from athena.registry import (
    DEFAULT_MAX_ATTEMPTS_PER_EXECUTION,
    DEFAULT_MAX_EXECUTIONS,
    ExecutionRegistry,
    ExecutionRegistryContract,
)


def _create(registry: ExecutionRegistry, index: int) -> dict:
    return registry.create(
        execution_id=f"execution-{index}",
        request_id=f"request-{index}",
        tool="run_agent",
    )


def test_default_limits_are_part_of_the_public_surface() -> None:
    registry = ExecutionRegistry()

    assert DEFAULT_MAX_EXECUTIONS == 256
    assert DEFAULT_MAX_ATTEMPTS_PER_EXECUTION == 64
    assert registry.max_executions == 256
    assert registry.max_attempts_per_execution == 64


def test_oldest_execution_is_evicted_with_its_request_index() -> None:
    registry = ExecutionRegistry(max_executions=2)
    _create(registry, 1)
    _create(registry, 2)
    _create(registry, 3)

    assert [entry["execution_id"] for entry in registry.list()] == [
        "execution-2",
        "execution-3",
    ]
    assert registry.get(execution_id="execution-1") is None
    assert registry.get(request_id="request-1") is None


def test_oldest_attempt_is_evicted_per_execution() -> None:
    registry = ExecutionRegistry(max_attempts_per_execution=2)
    _create(registry, 1)

    for index in range(3):
        registry.update_attempt(
            "execution-1",
            {"attempt_id": f"attempt-{index}", "state": "running"},
        )

    entry = registry.get(execution_id="execution-1")
    assert entry is not None
    assert [attempt["attempt_id"] for attempt in entry["attempts"]] == [
        "attempt-1",
        "attempt-2",
    ]


def test_entries_and_attempts_omit_prompt_output_response_and_credentials() -> None:
    secret = "credential-value-that-must-never-be-returned"
    registry = ExecutionRegistry()
    registry.create(
        execution_id="execution-safe",
        request_id=f"token-{secret}",
        tool="run_agent",
    )
    registry.update_attempt(
        "execution-safe",
        {
            "attempt_id": "attempt-safe",
            "state": "running",
            "prompt": secret,
            "response": secret,
            "output": {"nested": secret},
            "credential": secret,
            "api_token": secret,
        },
    )

    entry = registry.get(execution_id="execution-safe")
    assert entry is not None
    serialized = json.dumps(entry)
    attempt = entry["attempts"][0]
    assert secret not in serialized
    assert not {
        "prompt",
        "response",
        "output",
        "credential",
        "api_token",
    } & attempt.keys()
    assert str(entry["request_id"]).startswith("sha256:")


def test_raw_request_id_resolves_entry_even_when_public_value_is_hashed() -> None:
    raw_request_id = "raw-high-entropy-request-id-0123456789abcdef"
    registry = ExecutionRegistry()
    created = registry.create(
        execution_id="execution-raw-lookup",
        request_id=raw_request_id,
        tool="run_agent",
    )

    assert created["request_id"] != raw_request_id
    assert registry.get(request_id=raw_request_id) == created
    assert registry.get(request_id=created["request_id"]) is None


def test_get_and_list_return_defensive_copies() -> None:
    registry = ExecutionRegistry()
    created = _create(registry, 1)
    created["tool"] = "tampered"
    listed = registry.list()
    listed[0]["attempts"].append({"output": "tampered"})

    stored = registry.get(execution_id="execution-1")
    assert stored is not None
    assert stored["tool"] == "run_agent"
    assert stored["attempts"] == []


def test_list_limit_returns_most_recent_entries_in_stable_order() -> None:
    registry = ExecutionRegistry()
    for index in range(4):
        _create(registry, index)

    assert [entry["execution_id"] for entry in registry.list(limit=2)] == [
        "execution-2",
        "execution-3",
    ]


def test_cancel_uses_private_control_without_exposing_it() -> None:
    registry = ExecutionRegistry()
    control = CancellationToken()
    registry.create(
        execution_id="execution-cancel",
        request_id=17,
        tool="run_agent",
        control=control,
    )

    result = registry.request_cancel(request_id=17, reason="user_requested")
    entry = registry.get(execution_id="execution-cancel")

    assert result == {
        "found": True,
        "requested": True,
        "execution_id": "execution-cancel",
    }
    assert control.cancellation_requested
    assert entry is not None
    assert "control" not in entry


@pytest.mark.parametrize(
    ("parameter", "value"),
    [("max_executions", 0), ("max_attempts_per_execution", -1)],
)
def test_registry_limits_must_be_positive_integers(parameter: str, value: int) -> None:
    with pytest.raises(ValueError, match=parameter):
        ExecutionRegistry(**{parameter: value})


def test_public_implementation_satisfies_registry_contract() -> None:
    assert isinstance(ExecutionRegistry(), ExecutionRegistryContract)


def test_registry_imports_no_other_core_package_than_execution() -> None:
    package = Path(__file__).resolve().parents[1] / "athena" / "registry"
    imported_core_packages: set[str] = set()

    for module in package.glob("*.py"):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported_core_packages.add(node.module)
            elif isinstance(node, ast.Import):
                imported_core_packages.update(alias.name for alias in node.names)

    core_imports = {
        name
        for name in imported_core_packages
        if name == "athena" or name.startswith("athena.")
    }
    assert core_imports == {"athena.execution"}
