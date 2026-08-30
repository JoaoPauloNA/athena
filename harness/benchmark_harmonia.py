"""Benchmark MULTI-0 — planejamento e reserva sem I/O/IA."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from dataclasses import dataclass

from athena.harmonia import HarmoniaEngine, ResourceBudget, SubtaskSpec, TeamPlan
from athena.lease import ResourceLeaseManager

MIN_SAMPLES = 5
MAX_SAMPLES = 1_000
MIN_WARMUPS = 1
MAX_WARMUPS = 100
DEFAULT_PLAN_CEILING_MS = 5.0
DEFAULT_RESERVE_CEILING_MS = 5.0
_SEAL = "a" * 64


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    samples: int
    warmups: int
    guardrail: bool
    plan_ceiling_ms: float
    reserve_ceiling_ms: float


class _NoopExecutor:
    def execute(self, *, subtask, workspace_root, attempt_id):
        return 0, ()


def _bounded_int(value: str, *, minimum: int, maximum: int, name: str) -> int:
    parsed = int(value)
    if not minimum <= parsed <= maximum:
        raise argparse.ArgumentTypeError(
            f"{name} must be between {minimum} and {maximum}"
        )
    return parsed


def _bounded_ceiling(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("ceiling must be finite and non-negative")
    return parsed


def percentile(values: list[float], pct: float) -> float:
    if not values:
        raise ValueError("values must not be empty")
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def _sample_plan() -> TeamPlan:
    subtasks = tuple(
        SubtaskSpec(
            subtask_id=f"s{index:02d}",
            dependencies=(f"s{index - 1:02d}",) if index else (),
            worker_id=f"worker-{index % 3}",
            read_scope=(f"src/module{index}.py",),
            write_scope=(f"src/out{index}.txt",),
            operation_type="file_edit",
            resources=ResourceBudget(1, 64, 0, 1),
            seal_hash=_SEAL,
        )
        for index in range(8)
    )
    return TeamPlan(
        task_id="bench-task",
        subtasks=subtasks,
        max_parallelism=4,
        project_parallelism=4,
        aegis_parallelism=4,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--samples",
        type=lambda v: _bounded_int(v, minimum=MIN_SAMPLES, maximum=MAX_SAMPLES, name="samples"),
        default=30,
    )
    parser.add_argument(
        "--warmups",
        type=lambda v: _bounded_int(v, minimum=MIN_WARMUPS, maximum=MAX_WARMUPS, name="warmups"),
        default=3,
    )
    parser.add_argument("--guardrail", action="store_true")
    parser.add_argument(
        "--plan-ceiling-ms",
        type=_bounded_ceiling,
        default=DEFAULT_PLAN_CEILING_MS,
    )
    parser.add_argument(
        "--reserve-ceiling-ms",
        type=_bounded_ceiling,
        default=DEFAULT_RESERVE_CEILING_MS,
    )
    return parser


def measure_plan(engine: HarmoniaEngine, plan: TeamPlan) -> float:
    start = time.perf_counter_ns()
    engine.plan(plan)
    return (time.perf_counter_ns() - start) / 1_000_000


def measure_reserve(engine: HarmoniaEngine, plan: TeamPlan) -> float:
    start = time.perf_counter_ns()
    engine.reserve_only(plan)
    return (time.perf_counter_ns() - start) / 1_000_000


def build_report(config: BenchmarkConfig) -> dict:
    engine = HarmoniaEngine(
        workspace_root="/tmp/harmonia-bench",
        executor=_NoopExecutor(),
        lease_manager=ResourceLeaseManager(),
    )
    plan = _sample_plan()
    plan_samples: list[float] = []
    reserve_samples: list[float] = []

    for _ in range(config.warmups):
        measure_plan(engine, plan)
        measure_reserve(engine, plan)

    for _ in range(config.samples):
        plan_samples.append(measure_plan(engine, plan))
        reserve_samples.append(measure_reserve(engine, plan))

    report = {
        "schema": "athena.harmonia-benchmark",
        "schema_version": "1.0",
        "samples": config.samples,
        "warmups": config.warmups,
        "plan_ms": {
            "p50": statistics.median(plan_samples),
            "p95": percentile(plan_samples, 95),
            "max": max(plan_samples),
        },
        "reserve_ms": {
            "p50": statistics.median(reserve_samples),
            "p95": percentile(reserve_samples, 95),
            "max": max(reserve_samples),
        },
        "guardrail": config.guardrail,
    }
    if config.guardrail:
        failures = []
        if report["plan_ms"]["p95"] > config.plan_ceiling_ms:
            failures.append("plan_p95")
        if report["reserve_ms"]["p95"] > config.reserve_ceiling_ms:
            failures.append("reserve_p95")
        report["guardrail_pass"] = not failures
        report["guardrail_failures"] = failures
    return report


def main() -> int:
    args = build_parser().parse_args()
    config = BenchmarkConfig(
        samples=args.samples,
        warmups=args.warmups,
        guardrail=args.guardrail,
        plan_ceiling_ms=args.plan_ceiling_ms,
        reserve_ceiling_ms=args.reserve_ceiling_ms,
    )
    report = build_report(config)
    print(json.dumps(report, sort_keys=True))
    if config.guardrail and not report.get("guardrail_pass", True):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
