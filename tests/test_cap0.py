"""Aceitação focada CAP-0 no caminho Cápsula → Iris → bridge."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from athena.bridge import RunRequest, RunResult
from athena.capsule import CapsuleDenied, ExecutionCapsule, ExecutionPlan
from athena.execution import ExecutionDeadlines, ExecutionRecord, ExecutionState
from athena.iris import BASE_ENVIRONMENT_NAMES, LocalIrisBoundary
from athena.lease import DirectoryLeaseManager
from athena.profiles import ServiceProfile
from athena.router import AllAttemptsFailed, ComboAttempt, ComboRequest, ComboRouter
from tests.route0_support import routing_arguments, write_route_config

KEY = b"c" * 32


def _execution_plan(**changes: object) -> ExecutionPlan:
    values: dict[str, object] = {
        "schema_version": "athena.execution-plan/1",
        "contract_version": "cap-0/1",
        "task_id": "task-1",
        "execution_id": "execution-1",
        "attempt_id": "attempt-1",
        "provider_id": "provider-1",
        "access_mode": "local_cli",
        "command": ["/usr/bin/true"],
        "cwd": "/tmp/work",
        "environment_names": ["PATH"],
        "environment_values_digest": "a" * 64,
        "network_policy": "declared_offline",
        "resource_scope": ["/tmp/work"],
        "write_scope": ["/tmp/work"],
        "permissions": ["execute_local_cli"],
        "absolute_timeout_s": 10.0,
        "idle_timeout_s": None,
        "lease_timeout_s": 1.0,
        "termination_grace_s": 0.5,
        "use_pty": False,
        "fallback_declared": False,
        "tests": ["test-digest"],
        "log_level": "sanitized",
    }
    values.update(changes)
    return ExecutionPlan(**values)  # type: ignore[arg-type]


class Clock:
    def __init__(self, now: int = 2_000_000_000) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now


def test_execution_plan_normalizes_actual_sequences_to_tuples() -> None:
    plan = _execution_plan()

    assert plan.command == ("/usr/bin/true",)
    assert plan.environment_names == ("PATH",)
    assert plan.permissions == ("execute_local_cli",)


@pytest.mark.parametrize(
    "field",
    [
        "command",
        "environment_names",
        "resource_scope",
        "write_scope",
        "permissions",
        "tests",
    ],
)
@pytest.mark.parametrize(
    "malformed",
    ["SENSITIVE_INPUT", b"SENSITIVE_INPUT", {"SENSITIVE_INPUT": "value"}, 7],
)
def test_execution_plan_rejects_non_sequence_collection_shapes_without_echo(
    field: str, malformed: object
) -> None:
    with pytest.raises(TypeError) as raised:
        _execution_plan(**{field: malformed})

    assert str(raised.value) == "CAPSULE_INVALID"
    assert "SENSITIVE_INPUT" not in str(raised.value)


@pytest.mark.parametrize(
    "field",
    [
        "command",
        "environment_names",
        "resource_scope",
        "write_scope",
        "permissions",
        "tests",
    ],
)
@pytest.mark.parametrize("malformed", [["ok", 1], ["ok", ""]])
def test_execution_plan_rejects_malformed_collection_items(
    field: str, malformed: object
) -> None:
    with pytest.raises(ValueError, match="^CAPSULE_INVALID$"):
        _execution_plan(**{field: malformed})


@pytest.mark.parametrize("field", ["use_pty", "fallback_declared"])
@pytest.mark.parametrize("malformed", [0, 1, "false", None])
def test_execution_plan_rejects_non_boolean_flags(
    field: str, malformed: object
) -> None:
    with pytest.raises(TypeError, match="^CAPSULE_INVALID$"):
        _execution_plan(**{field: malformed})


@pytest.mark.parametrize(
    "malformed",
    ["a" * 63, "A" * 64, "g" * 64, 7],
)
def test_execution_plan_rejects_malformed_environment_digest(
    malformed: object,
) -> None:
    with pytest.raises(ValueError, match="^CAPSULE_INVALID$"):
        _execution_plan(environment_values_digest=malformed)


class RecordingRunner:
    def __init__(self, states: tuple[ExecutionState, ...] = (ExecutionState.COMPLETED,)) -> None:
        self.calls = 0
        self.requests: list[RunRequest] = []
        self._states = iter(states)

    def run(self, request, execution, lease, *, control=None):
        self.calls += 1
        self.requests.append(request)
        state = next(self._states)
        execution.transition(ExecutionState.STARTING)
        execution.transition(ExecutionState.RUNNING)
        execution.transition(state)
        return RunResult(
            tuple(request.command),
            Path(request.cwd),
            state,
            0 if state is ExecutionState.COMPLETED else 1,
            "ok" if state is ExecutionState.COMPLETED else "",
            "",
            0.0,
        )


class RecordingIris(LocalIrisBoundary):
    def __init__(self, runner, clock: Clock) -> None:
        super().__init__(runner, KEY, parent_environment={}, clock=clock)
        self.seal_ids: list[str] = []
        self.plan_digests: list[str] = []

    def prepare_attempt(self, *args, **kwargs):
        prepared = super().prepare_attempt(*args, **kwargs)
        capsule = prepared.authorization
        assert isinstance(capsule, ExecutionCapsule)
        self.seal_ids.append(capsule.seal.seal_id)
        self.plan_digests.append(capsule.plan_digest)
        return prepared


def _record(*, attempt_id: str = "attempt-1") -> ExecutionRecord:
    return ExecutionRecord(
        "provider-1",
        profile="code_agent",
        execution_id="execution-1",
        attempt_id=attempt_id,
        deadlines=ExecutionDeadlines(absolute_timeout_s=10),
    )


def _prepared(
    tmp_path: Path,
    *,
    runner: RecordingRunner | None = None,
    clock: Clock | None = None,
):
    selected_runner = runner or RecordingRunner()
    selected_clock = clock or Clock()
    iris = LocalIrisBoundary(
        selected_runner,
        KEY,
        parent_environment={"PATH": "/usr/bin:/bin", "OPENAI_API_KEY": "parent-secret"},
        clock=selected_clock,
    )
    record = _record()
    request = RunRequest(("/usr/bin/true",), tmp_path, env={"SAFE_CAP_VAR": "safe"})
    prepared = iris.prepare_attempt(
        request,
        record,
        fallback_declared=False,
        tests=("test-digest",),
    )
    return iris, selected_runner, selected_clock, record, prepared


def test_valid_capsule_reaches_runner_with_only_minimum_environment(tmp_path: Path) -> None:
    iris, runner, _, record, prepared = _prepared(tmp_path)

    result = iris.run(prepared, record, DirectoryLeaseManager())

    assert result.state is ExecutionState.COMPLETED
    assert runner.calls == 1
    child = dict(runner.requests[0].env)
    assert child["SAFE_CAP_VAR"] == "safe"
    assert "OPENAI_API_KEY" not in child
    assert set(child) <= set(BASE_ENVIRONMENT_NAMES) | {"PWD", "SAFE_CAP_VAR"}
    assert runner.requests[0].inherit_environment is False
    assert runner.requests[0].authorization is None


@pytest.mark.parametrize("variant", ["missing", "tampered", "mismatched", "expired"])
def test_invalid_capsule_never_reaches_runner(tmp_path: Path, variant: str) -> None:
    iris, runner, clock, record, prepared = _prepared(tmp_path)
    capsule = prepared.authorization
    assert isinstance(capsule, ExecutionCapsule)
    if variant == "missing":
        prepared = replace(prepared, authorization=None)
    elif variant == "tampered":
        prepared = replace(
            prepared,
            authorization=replace(capsule, plan_digest="f" * 64),
        )
    elif variant == "mismatched":
        prepared = replace(prepared, command=("/usr/bin/false",))
    else:
        clock.now += 31

    with pytest.raises(CapsuleDenied):
        iris.run(prepared, record, DirectoryLeaseManager())

    assert runner.calls == 0


def test_consumed_single_attempt_capsule_never_reaches_runner_twice(tmp_path: Path) -> None:
    iris, runner, _, record, prepared = _prepared(tmp_path)
    iris.run(prepared, record, DirectoryLeaseManager())

    with pytest.raises(CapsuleDenied, match="CAPSULE_CONSUMED"):
        iris.run(prepared, _record(), DirectoryLeaseManager())

    assert runner.calls == 1


@pytest.mark.parametrize(
    "environment",
    [
        {"OPENAI_API_KEY": "must-not-leak"},
        {"TOKEN": "must-not-leak"},
        {"PATH": "/untrusted"},
        {"safe": "one", "SAFE": "two"},
        {"ＳＡＦＥ": "unicode-confusable"},
    ],
)
def test_environment_smuggling_is_denied_before_spawn_without_value_leak(
    tmp_path: Path, environment: dict[str, str]
) -> None:
    runner = RecordingRunner()
    iris = LocalIrisBoundary(runner, KEY, parent_environment={}, clock=Clock())

    with pytest.raises(CapsuleDenied) as raised:
        iris.prepare_attempt(
            RunRequest(("/usr/bin/true",), tmp_path, env=environment),
            _record(),
            fallback_declared=False,
            tests=(),
        )

    assert runner.calls == 0
    assert "must-not-leak" not in str(raised.value)
    assert "unicode-confusable" not in str(raised.value)


def test_fallback_receives_distinct_exact_plan_and_seal(tmp_path: Path) -> None:
    runner = RecordingRunner((ExecutionState.FAILED, ExecutionState.COMPLETED))
    clock = Clock()
    iris = RecordingIris(runner, clock)
    combo = ComboRequest(
        attempts=(
            ComboAttempt("provider-1", RunRequest(("/usr/bin/false",), tmp_path)),
            ComboAttempt("provider-2", RunRequest(("/usr/bin/true",), tmp_path)),
        ),
        profile=ServiceProfile.CODE_AGENT,
        execution_id="execution-fallback",
    )

    result = ComboRouter(
        iris,
        DirectoryLeaseManager(),
        attempt_authorizer=iris,
    ).run(combo)

    assert result.state is ExecutionState.COMPLETED
    assert len(set(iris.seal_ids)) == 2
    assert len(set(iris.plan_digests)) == 2
    assert runner.calls == 2


def test_unsupported_network_policy_is_terminal_before_runner(tmp_path: Path) -> None:
    runner = RecordingRunner()
    iris = LocalIrisBoundary(
        runner,
        KEY,
        parent_environment={},
        network_policy="unrestricted",
        clock=Clock(),
    )

    with pytest.raises(CapsuleDenied, match="UNSUPPORTED_NETWORK_POLICY"):
        iris.prepare_attempt(
            RunRequest(("/usr/bin/true",), tmp_path),
            _record(),
            fallback_declared=False,
            tests=(),
        )

    assert runner.calls == 0


def test_real_mcp_path_runs_with_minimum_child_environment(tmp_path: Path) -> None:
    config_dir = write_route_config(
        tmp_path / "config", providers=("cap0-local",)
    )
    execution_id = "cap0-real-env"
    arguments = {
        **routing_arguments(),
        "execution_id": execution_id,
        "attempts": [
            {
                "provider": "cap0-local",
                "command": ["/usr/bin/env"],
                "cwd": str(tmp_path),
                "env": {"SAFE_CAP_VAR": "visible"},
            }
        ],
    }
    message = {
        "jsonrpc": "2.0",
        "id": "cap0-request",
        "method": "tools/call",
        "params": {
            "name": "run_combo",
            "arguments": arguments,
        },
    }
    parent = dict(os.environ)
    parent["OPENAI_API_KEY"] = "parent-only-secret"
    parent["ATHENA_CONFIG_DIR"] = str(config_dir)
    process = subprocess.Popen(
        (sys.executable, "-m", "athena"),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=Path(__file__).parents[1],
        env=parent,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    process.stdin.write(json.dumps(message) + "\n")
    process.stdin.flush()
    response = json.loads(process.stdout.readline())
    process.stdin.close()
    assert process.wait(timeout=10) == 0
    tool = response["result"]
    payload = json.loads(tool["content"][0]["text"])
    assert payload["execution_id"] == execution_id
    lines = payload["result"]["stdout"].splitlines()
    names = {line.split("=", 1)[0] for line in lines}
    assert "SAFE_CAP_VAR" in names
    assert "OPENAI_API_KEY" not in names
    assert names <= set(BASE_ENVIRONMENT_NAMES) | {"PWD", "SAFE_CAP_VAR"}


def test_router_sanitizes_authorization_denial_and_releases_lease(tmp_path: Path) -> None:
    runner = RecordingRunner()
    iris = LocalIrisBoundary(runner, KEY, parent_environment={}, clock=Clock())
    lease = DirectoryLeaseManager()
    combo = ComboRequest(
        attempts=(
            ComboAttempt(
                "provider-1",
                RunRequest(("/usr/bin/true",), tmp_path, env={"TOKEN": "private"}),
            ),
        ),
        profile=ServiceProfile.CODE_AGENT,
        execution_id="denied-execution",
    )

    with pytest.raises(AllAttemptsFailed) as raised:
        ComboRouter(iris, lease, attempt_authorizer=iris).run(combo)

    assert str(raised.value) == "ENVIRONMENT_NAME_SECRET_LIKE"
    assert "private" not in str(raised.value)
    assert runner.calls == 0
    acquired = lease.acquire(tmp_path, "next", "next-attempt", timeout=0)
    lease.release(acquired, "next", "next-attempt")
