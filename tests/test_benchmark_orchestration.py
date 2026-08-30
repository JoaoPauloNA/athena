"""Deterministic tests for the orchestration performance harness."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from io import StringIO
from pathlib import Path

import pytest

from athena.config_loader import ConfigSnapshotCache
from athena.zeus.persistence import ZeusRegistrySnapshotCache
from harness import benchmark_orchestration as benchmark


def _config(*, guardrail: bool = True) -> benchmark.BenchmarkConfig:
    return benchmark.BenchmarkConfig(
        samples=5,
        warmups=2,
        guardrail=guardrail,
        bridge_ceiling_ms=30.0,
        mcp_incremental_ceiling_ms=5.0,
    )


def _response(
    request_id: object, execution_id: str, *, state: str = "completed"
) -> dict:
    payload = {
        "execution_id": execution_id,
        "result": {"state": state, "exit_code": 0},
    }
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {"content": [{"type": "text", "text": json.dumps(payload)}]},
    }


def test_percentile_and_statistics_use_nearest_rank_and_exact_count() -> None:
    values_ns = [1_000_000 * value for value in range(1, 21)]

    assert benchmark.percentile(list(range(1, 21)), 95) == 19
    assert benchmark.summarize_ns(values_ns) == {
        "samples": 20,
        "mean_ms": 10.5,
        "median_ms": 10.5,
        "p95_ms": 19.0,
        "min_ms": 1.0,
        "max_ms": 20.0,
    }
    with pytest.raises(ValueError, match="must not be empty"):
        benchmark.summarize_ns([])
    with pytest.raises(ValueError, match="finite"):
        benchmark.percentile([1.0, float("nan")], 95)


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--samples", "4"),
        ("--samples", "1001"),
        ("--warmups", "0"),
        ("--warmups", "101"),
        ("--bridge-ceiling-ms", "nan"),
        ("--bridge-ceiling-ms", "1001"),
        ("--mcp-incremental-ceiling-ms", "-0.1"),
    ],
)
def test_argument_bounds_are_enforced(option: str, value: str) -> None:
    parser = benchmark.build_parser()
    with pytest.raises(SystemExit) as caught:
        parser.parse_args([option, value])
    assert caught.value.code == 2


def test_argument_bounds_accept_safe_edges() -> None:
    namespace = benchmark.build_parser().parse_args(
        [
            "--samples",
            "5",
            "--warmups",
            "1",
            "--bridge-ceiling-ms",
            "0",
            "--mcp-incremental-ceiling-ms",
            "1000",
        ]
    )
    assert namespace.samples == 5
    assert namespace.warmups == 1
    assert namespace.bridge_ceiling_ms == 0.0
    assert namespace.mcp_incremental_ceiling_ms == 1000.0


def test_json_schema_is_stable_and_contains_exact_sample_counts() -> None:
    report = benchmark.build_report(
        config=_config(),
        direct_ns=[1_000_000] * 5,
        bridge_ns=[3_000_000] * 5,
        mcp_ns=[4_000_000] * 5,
        cleanup=benchmark.CleanupResult(0, False, False),
        environment={"fixed": True},
    )

    assert list(report) == [
        "schema",
        "environment",
        "configuration",
        "paths",
        "derived",
        "guardrail",
        "cleanup",
    ]
    assert report["schema"] == {
        "name": "athena.orchestration-benchmark",
        "version": "1.0",
    }
    assert report["environment"] == {"fixed": True}
    assert report["configuration"]["samples"] == 5
    assert {item["samples"] for item in report["paths"].values()} == {5}
    assert {item["samples"] for item in report["derived"].values()} == {5}
    assert report["cleanup"]["terminal_runs_validated"] == 7
    assert json.dumps(report, sort_keys=True) == json.dumps(report, sort_keys=True)


def test_build_report_rejects_inexact_sample_count() -> None:
    with pytest.raises(ValueError, match="exactly"):
        benchmark.build_report(
            config=_config(),
            direct_ns=[1] * 4,
            bridge_ns=[2] * 5,
            mcp_ns=[3] * 5,
            cleanup=benchmark.CleanupResult(0, False, False),
        )


def test_guardrail_pass_and_fail_name_the_failed_metric() -> None:
    passed = benchmark.evaluate_guardrail({"p95_ms": 30.0}, {"p95_ms": 5.0}, _config())
    failed = benchmark.evaluate_guardrail(
        {"p95_ms": 30.000001}, {"p95_ms": 5.000001}, _config()
    )

    assert passed["status"] == "passed"
    assert passed["failed_metrics"] == []
    assert failed["status"] == "failed"
    assert failed["failed_metrics"] == [
        "bridge_over_direct_p95_ms",
        "incremental_mcp_over_bridge_p95_ms",
    ]
    assert "not_future_slo" in failed["kind"]


def test_guardrail_disabled_does_not_silently_approve_policy() -> None:
    result = benchmark.evaluate_guardrail(
        {"p95_ms": 999.0}, {"p95_ms": 999.0}, _config(guardrail=False)
    )
    assert result["status"] == "not_evaluated"
    assert result["failed_metrics"] == []


def test_protocol_correlation_error_and_terminal_state_validation() -> None:
    benchmark.validate_run_combo_response(
        _response("request-1", "execution-1"),
        expected_id="request-1",
        expected_execution_id="execution-1",
    )
    with pytest.raises(benchmark.BenchmarkError, match="response id"):
        benchmark.validate_run_combo_response(
            _response("wrong", "execution-1"),
            expected_id="request-1",
            expected_execution_id="execution-1",
        )
    with pytest.raises(benchmark.BenchmarkError, match="JSON-RPC error"):
        benchmark.validate_run_combo_response(
            {"jsonrpc": "2.0", "id": "request-1", "error": {"code": -32000}},
            expected_id="request-1",
            expected_execution_id="execution-1",
        )
    with pytest.raises(benchmark.BenchmarkError, match="terminal state"):
        benchmark.validate_run_combo_response(
            _response("request-1", "execution-1", state="running"),
            expected_id="request-1",
            expected_execution_id="execution-1",
        )


class FakeProcess:
    def __init__(self, outcomes: list[object]) -> None:
        self.stdin = StringIO()
        self.stdout = StringIO()
        self.stderr = StringIO()
        self._outcomes = iter(outcomes)
        self.returncode = None
        self.terminated = False
        self.killed = False

    def wait(self, timeout: float) -> int:
        outcome = next(self._outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        self.returncode = int(outcome)
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def poll(self) -> int | None:
        return self.returncode


def test_cleanup_closes_stdin_reaps_process_and_reports_no_child() -> None:
    process = FakeProcess([0])
    result = benchmark.close_process(process)  # type: ignore[arg-type]

    assert process.stdin.closed
    assert process.stdout.closed
    assert process.stderr.closed
    assert result == benchmark.CleanupResult(0, False, False)
    assert not process.terminated and not process.killed


def test_cleanup_escalates_and_still_proves_process_reaped() -> None:
    process = FakeProcess(
        [
            subprocess.TimeoutExpired("athena", 1),
            subprocess.TimeoutExpired("athena", 1),
            -9,
        ]
    )
    result = benchmark.close_process(process, timeout_s=1)  # type: ignore[arg-type]

    assert process.terminated and process.killed
    assert result == benchmark.CleanupResult(-9, False, True)


def test_client_reaps_child_if_initialization_raises_and_discards_stderr(
    monkeypatch,
) -> None:
    process = FakeProcess([0])
    popen_options: dict[str, object] = {}

    def fake_popen(*args: object, **kwargs: object) -> FakeProcess:
        popen_options.update(kwargs)
        return process

    def fail_initialization(self: benchmark.PersistentMCPClient) -> None:
        raise benchmark.BenchmarkError("deterministic initialization failure")

    monkeypatch.setattr(benchmark.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        benchmark.PersistentMCPClient, "_initialize", fail_initialization
    )

    with pytest.raises(benchmark.BenchmarkError, match="initialization failure"):
        benchmark.PersistentMCPClient(benchmark.Path("."))

    assert popen_options["stderr"] is subprocess.DEVNULL
    assert process.stdin.closed
    assert process.stdout.closed
    assert process.stderr.closed
    assert process.poll() == 0
    assert not process.terminated and not process.killed


def test_main_returns_two_and_emits_failed_metric_for_guardrail(monkeypatch) -> None:
    report = {
        "guardrail": {
            "status": "failed",
            "failed_metrics": ["bridge_over_direct_p95_ms"],
        }
    }
    monkeypatch.setattr(benchmark, "run_benchmark", lambda config: report)
    output = StringIO()

    exit_code = benchmark.main(["--guardrail"], stdout=output)

    assert exit_code == 2
    assert json.loads(output.getvalue())["guardrail"]["failed_metrics"] == [
        "bridge_over_direct_p95_ms"
    ]


def test_failure_document_does_not_echo_paths_or_environment_values() -> None:
    private_value = "/private/home/example/credential-value"
    document = benchmark._failure_document(OSError(private_value))
    serialized = json.dumps(document)

    assert private_value not in serialized
    assert document["error"]["type"] == "OSError"


def test_config_conversion_is_explicit() -> None:
    namespace = argparse.Namespace(
        samples=7,
        warmups=2,
        guardrail=True,
        bridge_ceiling_ms=12.0,
        mcp_incremental_ceiling_ms=3.0,
    )
    assert benchmark.config_from_namespace(namespace) == benchmark.BenchmarkConfig(
        7, 2, True, 12.0, 3.0
    )


def test_benchmark_routing_arguments_are_strict_and_complete() -> None:
    assert benchmark.benchmark_routing_arguments() == {
        "task_type": "backend",
        "primary_domain": "software.backend",
        "risk_level": "low",
        "required_capabilities": ["execute"],
    }


def test_write_benchmark_route_config_produces_valid_route0_snapshot(
    tmp_path: Path,
) -> None:
    config_dir = benchmark.write_benchmark_route_config(tmp_path / "route-config")

    snapshot = ConfigSnapshotCache(config_dir).refresh()
    registry = ZeusRegistrySnapshotCache(config_dir).refresh()
    inventory = json.loads((config_dir / "cache" / "inventory.json").read_text())

    assert snapshot["providers"]["benchmark-local"]["approved"] is True
    assert registry.snapshot()["benchmark-agent"].lifecycle == "approved"
    assert registry.snapshot()["benchmark-agent"].capabilities == frozenset({"execute"})
    assert inventory["entries"] == [{"provider_id": "benchmark-local", "healthy": True}]


def test_persistent_client_passes_child_env_without_mutating_parent(
    monkeypatch,
) -> None:
    process = FakeProcess([0])
    popen_options: dict[str, object] = {}
    parent_before = os.environ.copy()

    def fake_popen(*args: object, **kwargs: object) -> FakeProcess:
        popen_options.update(kwargs)
        return process

    monkeypatch.setattr(benchmark.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(benchmark.PersistentMCPClient, "_initialize", lambda self: None)

    client = benchmark.PersistentMCPClient(
        benchmark.Path("."),
        env={"ATHENA_CONFIG_DIR": "/tmp/athena-benchmark-route-config"},
    )
    client.close()

    child_env = popen_options["env"]
    assert isinstance(child_env, dict)
    assert child_env["ATHENA_CONFIG_DIR"] == "/tmp/athena-benchmark-route-config"
    assert os.environ == parent_before


def test_run_combo_includes_routing_context_in_payload(monkeypatch) -> None:
    payloads: list[dict[str, object]] = []
    client = object.__new__(benchmark.PersistentMCPClient)
    client._closed = False

    def capture_send(payload: dict[str, object]) -> None:
        payloads.append(payload)

    def fake_receive() -> dict[str, object]:
        request_id = payloads[-1]["id"]
        execution_id = payloads[-1]["params"]["arguments"]["execution_id"]  # type: ignore[index]
        return _response(request_id, execution_id)

    monkeypatch.setattr(client, "_send", capture_send)
    monkeypatch.setattr(client, "_receive", fake_receive)

    client.run_combo(("/usr/bin/true",), benchmark.Path("."), 7)

    arguments = payloads[-1]["params"]["arguments"]  # type: ignore[index]
    assert arguments["task_type"] == "backend"
    assert arguments["primary_domain"] == "software.backend"
    assert arguments["risk_level"] == "low"
    assert arguments["required_capabilities"] == ["execute"]
    assert arguments["attempts"][0]["provider"] == "benchmark-local"
