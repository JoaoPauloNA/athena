"""Characterize orchestration overhead without changing the production path."""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import selectors
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from athena.bridge import LocalBridgeRunner, RunRequest
from athena.config_loader import build_manifest, write_snapshot
from athena.execution import ExecutionRecord, ExecutionState
from athena.lease import DirectoryLeaseManager
from athena.zeus import AgentRecord, ZeusRegistry
from athena.zeus.persistence import save_registry

SCHEMA_NAME = "athena.orchestration-benchmark"
SCHEMA_VERSION = "1.0"
MIN_SAMPLES = 5
MAX_SAMPLES = 1_000
MIN_WARMUPS = 1
MAX_WARMUPS = 100
MIN_CEILING_MS = 0.0
MAX_CEILING_MS = 1_000.0
DEFAULT_BRIDGE_CEILING_MS = 30.0
DEFAULT_MCP_INCREMENTAL_CEILING_MS = 5.0
_BENCHMARK_PROVIDER = "benchmark-local"
_BENCHMARK_AGENT = "benchmark-agent"
_RESPONSE_TIMEOUT_S = 10.0
_SHUTDOWN_TIMEOUT_S = 5.0


class BenchmarkError(RuntimeError):
    """Report a benchmark or protocol invariant failure without raw payloads."""


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    """Validated benchmark controls."""

    samples: int
    warmups: int
    guardrail: bool
    bridge_ceiling_ms: float
    mcp_incremental_ceiling_ms: float


@dataclass(frozen=True, slots=True)
class CleanupResult:
    """Sanitized proof that the persistent server child was reaped."""

    exit_code: int
    process_alive: bool
    forced: bool


def _bounded_int(value: str, *, minimum: int, maximum: int, name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{name} must be an integer") from exc
    if not minimum <= parsed <= maximum:
        raise argparse.ArgumentTypeError(
            f"{name} must be between {minimum} and {maximum}"
        )
    return parsed


def _bounded_ceiling(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("ceiling must be a number") from exc
    if not math.isfinite(parsed) or not MIN_CEILING_MS <= parsed <= MAX_CEILING_MS:
        raise argparse.ArgumentTypeError(
            f"ceiling must be finite and between {MIN_CEILING_MS:g} and "
            f"{MAX_CEILING_MS:g} ms"
        )
    return parsed


def build_parser() -> argparse.ArgumentParser:
    """Build the bounded command-line contract."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--samples",
        type=lambda value: _bounded_int(
            value, minimum=MIN_SAMPLES, maximum=MAX_SAMPLES, name="samples"
        ),
        default=30,
    )
    parser.add_argument(
        "--warmups",
        type=lambda value: _bounded_int(
            value, minimum=MIN_WARMUPS, maximum=MAX_WARMUPS, name="warmups"
        ),
        default=3,
    )
    parser.add_argument("--guardrail", action="store_true")
    parser.add_argument(
        "--bridge-ceiling-ms",
        type=_bounded_ceiling,
        default=DEFAULT_BRIDGE_CEILING_MS,
    )
    parser.add_argument(
        "--mcp-incremental-ceiling-ms",
        type=_bounded_ceiling,
        default=DEFAULT_MCP_INCREMENTAL_CEILING_MS,
    )
    return parser


def config_from_namespace(namespace: argparse.Namespace) -> BenchmarkConfig:
    """Convert parser output to an immutable configuration."""
    return BenchmarkConfig(
        samples=namespace.samples,
        warmups=namespace.warmups,
        guardrail=namespace.guardrail,
        bridge_ceiling_ms=namespace.bridge_ceiling_ms,
        mcp_incremental_ceiling_ms=namespace.mcp_incremental_ceiling_ms,
    )


def percentile(values: Sequence[float], percentile_value: float) -> float:
    """Return a nearest-rank percentile for a non-empty finite sample."""
    if not values:
        raise ValueError("values must not be empty")
    if not 0.0 < percentile_value <= 100.0:
        raise ValueError("percentile must be greater than zero and at most 100")
    normalized = [float(value) for value in values]
    if not all(math.isfinite(value) for value in normalized):
        raise ValueError("values must be finite")
    ordered = sorted(normalized)
    rank = math.ceil((percentile_value / 100.0) * len(ordered))
    return ordered[max(0, rank - 1)]


def summarize_ns(values_ns: Sequence[int | float]) -> dict[str, int | float]:
    """Summarize nanosecond samples as rounded milliseconds."""
    if not values_ns:
        raise ValueError("values must not be empty")
    values_ms = [float(value) / 1_000_000.0 for value in values_ns]
    if not all(math.isfinite(value) for value in values_ms):
        raise ValueError("values must be finite")

    def rounded(value: float) -> float:
        return round(value, 6)

    return {
        "samples": len(values_ms),
        "mean_ms": rounded(statistics.fmean(values_ms)),
        "median_ms": rounded(statistics.median(values_ms)),
        "p95_ms": rounded(percentile(values_ms, 95.0)),
        "min_ms": rounded(min(values_ms)),
        "max_ms": rounded(max(values_ms)),
    }


def paired_difference(left: Sequence[int], right: Sequence[int]) -> list[int]:
    """Subtract corresponding samples while preserving signed overhead."""
    if len(left) != len(right) or not left:
        raise ValueError("paired samples must be non-empty and equal in length")
    return [left_value - right_value for left_value, right_value in zip(left, right)]


def measure_samples(
    operation: Callable[[], None],
    *,
    samples: int,
    warmups: int,
    clock: Callable[[], int] = time.perf_counter_ns,
) -> list[int]:
    """Warm an operation, then retain exactly the requested timed samples."""
    for _ in range(warmups):
        operation()
    measured: list[int] = []
    for _ in range(samples):
        started = clock()
        operation()
        elapsed = clock() - started
        if elapsed < 0:
            raise BenchmarkError("monotonic clock moved backwards")
        measured.append(elapsed)
    return measured


def _validate_rpc_response(response: object, expected_id: object) -> dict[str, Any]:
    if not isinstance(response, dict) or response.get("jsonrpc") != "2.0":
        raise BenchmarkError("invalid JSON-RPC response envelope")
    if response.get("id") != expected_id:
        raise BenchmarkError("JSON-RPC response id did not match the request")
    if "error" in response:
        raise BenchmarkError("Athena MCP returned a JSON-RPC error")
    result = response.get("result")
    if not isinstance(result, dict):
        raise BenchmarkError("JSON-RPC response result was not an object")
    return result


def validate_run_combo_response(
    response: object, *, expected_id: object, expected_execution_id: str
) -> None:
    """Validate correlation and the successful terminal state without logging data."""
    tool_result = _validate_rpc_response(response, expected_id)
    if tool_result.get("isError") is True:
        raise BenchmarkError("run_combo returned a tool error")
    content = tool_result.get("content")
    if not isinstance(content, list) or len(content) != 1:
        raise BenchmarkError("run_combo content shape was invalid")
    item = content[0]
    if not isinstance(item, dict) or not isinstance(item.get("text"), str):
        raise BenchmarkError("run_combo text result was invalid")
    try:
        payload = json.loads(item["text"])
    except json.JSONDecodeError as exc:
        raise BenchmarkError("run_combo text result was not JSON") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("execution_id") != expected_execution_id
    ):
        raise BenchmarkError("run_combo execution id did not match the request")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise BenchmarkError("run_combo payload result was invalid")
    if result.get("state") != ExecutionState.COMPLETED.value:
        raise BenchmarkError("run_combo did not reach the completed terminal state")
    if result.get("exit_code") != 0:
        raise BenchmarkError("run_combo harmless command returned a nonzero exit code")


def benchmark_routing_arguments() -> dict[str, object]:
    """Return the strict ROUTE-0 context required by every benchmark run_combo."""
    return {
        "task_type": "backend",
        "primary_domain": "software.backend",
        "risk_level": "low",
        "required_capabilities": ["execute"],
    }


def write_benchmark_route_config(config_dir: Path) -> Path:
    """Publish an isolated ROUTE-0 snapshot for the benchmark MCP child."""
    config_dir.mkdir(parents=True, exist_ok=True)
    provider_doc = {
        _BENCHMARK_PROVIDER: {
            "mode": "agent_cli",
            "runtime_class": "local",
            "enabled": True,
            "approved": True,
            "command": _BENCHMARK_PROVIDER,
        }
    }
    (config_dir / "providers.json").write_text(
        json.dumps(provider_doc, sort_keys=True), encoding="utf-8"
    )
    (config_dir / "functions.json").write_text("{}", encoding="utf-8")
    write_snapshot(config_dir, build_manifest(config_dir))

    registry = ZeusRegistry()
    registry.create_version(
        [
            AgentRecord(
                _BENCHMARK_AGENT,
                "software.backend",
                "benchmark",
                frozenset({"execute"}),
                "local",
                lifecycle="approved",
            )
        ],
        action="create",
    )
    save_registry(registry, config_dir)
    cache_dir = config_dir / "cache"
    cache_dir.mkdir()
    (cache_dir / "inventory.json").write_text(
        json.dumps(
            {"entries": [{"provider_id": _BENCHMARK_PROVIDER, "healthy": True}]},
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return config_dir


def close_process(
    process: subprocess.Popen[str], *, timeout_s: float = _SHUTDOWN_TIMEOUT_S
) -> CleanupResult:
    """Close stdin, reap the direct child, and report whether force was needed."""
    forced = False
    if process.stdin is not None and not process.stdin.closed:
        process.stdin.close()
    try:
        exit_code = process.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        forced = True
        process.terminate()
        try:
            exit_code = process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            process.kill()
            exit_code = process.wait(timeout=1.0)
    finally:
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
    return CleanupResult(
        exit_code=exit_code,
        process_alive=process.poll() is None,
        forced=forced,
    )


class PersistentMCPClient:
    """One real Athena stdio process shared by all MCP benchmark samples."""

    def __init__(
        self,
        project_root: Path,
        *,
        env: Mapping[str, str] | None = None,
    ) -> None:
        child_env = os.environ.copy()
        if env is not None:
            child_env.update(env)
        self._process = subprocess.Popen(
            (sys.executable, "-m", "athena"),
            cwd=project_root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            start_new_session=os.name == "posix",
            env=child_env,
        )
        self._closed = False
        try:
            self._initialize()
        except BaseException:
            self._closed = True
            close_process(self._process)
            raise

    def _send(self, payload: dict[str, object]) -> None:
        if self._process.stdin is None or self._process.stdin.closed:
            raise BenchmarkError("Athena MCP stdin is unavailable")
        self._process.stdin.write(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        )
        self._process.stdin.flush()

    def _receive(self) -> dict[str, Any]:
        stdout = self._process.stdout
        if stdout is None:
            raise BenchmarkError("Athena MCP stdout is unavailable")
        selector = selectors.DefaultSelector()
        try:
            selector.register(stdout, selectors.EVENT_READ)
            if not selector.select(timeout=_RESPONSE_TIMEOUT_S):
                raise BenchmarkError("timed out waiting for Athena MCP response")
        finally:
            selector.close()
        line = stdout.readline()
        if not line:
            raise BenchmarkError("Athena MCP closed stdout before responding")
        try:
            response = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BenchmarkError("Athena MCP response was not valid JSON") from exc
        if not isinstance(response, dict):
            raise BenchmarkError("Athena MCP response was not an object")
        return response

    def _initialize(self) -> None:
        request_id = "benchmark-initialize"
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "initialize",
                "params": {},
            }
        )
        _validate_rpc_response(self._receive(), request_id)
        self._send(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            }
        )

    def run_combo(self, command: tuple[str, ...], cwd: Path, sequence: int) -> None:
        request_id = f"benchmark-request-{sequence}"
        execution_id = f"benchmark-execution-{sequence}"
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {
                    "name": "run_combo",
                    "arguments": {
                        "execution_id": execution_id,
                        **benchmark_routing_arguments(),
                        "attempts": [
                            {
                                "provider": _BENCHMARK_PROVIDER,
                                "command": list(command),
                                "cwd": str(cwd),
                            }
                        ],
                    },
                },
            }
        )
        validate_run_combo_response(
            self._receive(),
            expected_id=request_id,
            expected_execution_id=execution_id,
        )

    def close(self) -> CleanupResult:
        if self._closed:
            raise BenchmarkError("Athena MCP client was already closed")
        self._closed = True
        return close_process(self._process)


def evaluate_guardrail(
    bridge_overhead: dict[str, int | float],
    mcp_incremental: dict[str, int | float],
    config: BenchmarkConfig,
) -> dict[str, object]:
    """Evaluate characterization ceilings and name every failed metric."""
    limits = {
        "bridge_over_direct_p95_ms": config.bridge_ceiling_ms,
        "incremental_mcp_over_bridge_p95_ms": config.mcp_incremental_ceiling_ms,
    }
    observed = {
        "bridge_over_direct_p95_ms": float(bridge_overhead["p95_ms"]),
        "incremental_mcp_over_bridge_p95_ms": float(mcp_incremental["p95_ms"]),
    }
    failures = [name for name, limit in limits.items() if observed[name] > limit]
    return {
        "enabled": config.guardrail,
        "kind": "characterization_ceiling_not_future_slo",
        "status": "failed"
        if config.guardrail and failures
        else ("passed" if config.guardrail else "not_evaluated"),
        "limits_ms": limits,
        "failed_metrics": failures if config.guardrail else [],
    }


def environment_metadata() -> dict[str, object]:
    """Return allowlisted, non-identifying environment facts only."""
    clock = time.get_clock_info("perf_counter")
    return {
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "os": platform.system(),
        "os_release": platform.release(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "timer": "perf_counter_ns",
        "timer_resolution_ns": round(clock.resolution * 1_000_000_000),
    }


def build_report(
    *,
    config: BenchmarkConfig,
    direct_ns: Sequence[int],
    bridge_ns: Sequence[int],
    mcp_ns: Sequence[int],
    cleanup: CleanupResult,
    environment: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build the stable versioned JSON document from exact sample vectors."""
    if not (len(direct_ns) == len(bridge_ns) == len(mcp_ns) == config.samples):
        raise ValueError("all paths must contain exactly the configured sample count")
    bridge_overhead = summarize_ns(paired_difference(bridge_ns, direct_ns))
    mcp_incremental = summarize_ns(paired_difference(mcp_ns, bridge_ns))
    guardrail = evaluate_guardrail(bridge_overhead, mcp_incremental, config)
    return {
        "schema": {"name": SCHEMA_NAME, "version": SCHEMA_VERSION},
        "environment": environment
        if environment is not None
        else environment_metadata(),
        "configuration": {
            "samples": config.samples,
            "warmups": config.warmups,
            "command": "true",
            "mcp_session": "persistent",
        },
        "paths": {
            "direct_subprocess": summarize_ns(direct_ns),
            "local_bridge_runner": summarize_ns(bridge_ns),
            "persistent_mcp_run_combo": summarize_ns(mcp_ns),
        },
        "derived": {
            "bridge_over_direct": bridge_overhead,
            "incremental_mcp_over_bridge": mcp_incremental,
        },
        "guardrail": guardrail,
        "cleanup": {
            "mcp_exit_code": cleanup.exit_code,
            "mcp_process_alive": cleanup.process_alive,
            "forced": cleanup.forced,
            "terminal_runs_validated": config.samples + config.warmups,
        },
    }


def run_benchmark(config: BenchmarkConfig) -> dict[str, object]:
    """Measure the same harmless command through all three required paths."""
    project_root = Path(__file__).resolve().parents[1]
    true_command = shutil.which("true")
    if true_command is None:
        raise BenchmarkError("the harmless 'true' command is unavailable")
    command = (true_command,)

    def direct_operation() -> None:
        result = subprocess.run(
            command,
            cwd=project_root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=_RESPONSE_TIMEOUT_S,
        )
        if result.returncode != 0:
            raise BenchmarkError("direct harmless command returned a nonzero exit code")

    runner = LocalBridgeRunner()
    lease = DirectoryLeaseManager()

    def bridge_operation() -> None:
        execution = ExecutionRecord("benchmark-local")
        result = runner.run(RunRequest(command, project_root), execution, lease)
        if result.state is not ExecutionState.COMPLETED or result.exit_code != 0:
            raise BenchmarkError(
                "bridge harmless command did not complete successfully"
            )

    direct_ns = measure_samples(
        direct_operation, samples=config.samples, warmups=config.warmups
    )
    bridge_ns = measure_samples(
        bridge_operation, samples=config.samples, warmups=config.warmups
    )

    with tempfile.TemporaryDirectory(
        prefix="athena-benchmark-route-"
    ) as config_dir_str:
        config_dir = write_benchmark_route_config(Path(config_dir_str))
        client = PersistentMCPClient(
            project_root,
            env={"ATHENA_CONFIG_DIR": str(config_dir)},
        )
        sequence = iter(range(config.samples + config.warmups))

        def mcp_operation() -> None:
            client.run_combo(command, project_root, next(sequence))

        try:
            mcp_ns = measure_samples(
                mcp_operation, samples=config.samples, warmups=config.warmups
            )
        finally:
            cleanup = client.close()
    if cleanup.process_alive or cleanup.forced or cleanup.exit_code != 0:
        raise BenchmarkError("persistent Athena MCP process did not close cleanly")
    return build_report(
        config=config,
        direct_ns=direct_ns,
        bridge_ns=bridge_ns,
        mcp_ns=mcp_ns,
        cleanup=cleanup,
    )


def _failure_document(exc: Exception) -> dict[str, object]:
    return {
        "schema": {"name": SCHEMA_NAME, "version": SCHEMA_VERSION},
        "error": {
            "type": type(exc).__name__,
            "message": "benchmark failed without sensitive diagnostic output",
        },
    }


def main(argv: Sequence[str] | None = None, *, stdout: TextIO | None = None) -> int:
    """Run the benchmark, emitting exactly one JSON document on stdout."""
    output = stdout or sys.stdout
    config = config_from_namespace(build_parser().parse_args(argv))
    try:
        report = run_benchmark(config)
    except (BenchmarkError, OSError, subprocess.SubprocessError, ValueError) as exc:
        print(json.dumps(_failure_document(exc), sort_keys=True), file=output)
        return 1
    print(json.dumps(report, sort_keys=True), file=output)
    guardrail = report["guardrail"]
    assert isinstance(guardrail, dict)
    return 2 if guardrail["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
