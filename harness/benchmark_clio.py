"""Benchmark CLIO-0 — enqueue p95 e modo none."""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import time
from dataclasses import dataclass
from pathlib import Path

from athena.clio.contracts import LEVEL_NONE, LEVEL_TECHNICAL, TechnicalEvent
from athena.clio.emitter import ClioEmitter
from athena.clio.policy import resolve_level
from athena.clio.producer import ClioProducer
from athena.clio.sanitizer import build_technical_payload

MIN_SAMPLES = 5
MAX_SAMPLES = 1_000
MIN_WARMUPS = 1
MAX_WARMUPS = 100
DEFAULT_ENQUEUE_CEILING_MS = 1.0
DEFAULT_NONE_CEILING_MS = 0.05


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    samples: int
    warmups: int
    guardrail: bool
    enqueue_ceiling_ms: float
    none_ceiling_ms: float


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
        raise argparse.ArgumentTypeError("ceiling must be a finite non-negative number")
    return parsed


def percentile(values: list[float], pct: float) -> float:
    if not values:
        raise ValueError("values must not be empty")
    if not all(math.isfinite(v) for v in values):
        raise ValueError("values must be finite")
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


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
        "--enqueue-ceiling-ms",
        type=_bounded_ceiling,
        default=DEFAULT_ENQUEUE_CEILING_MS,
    )
    parser.add_argument(
        "--none-ceiling-ms",
        type=_bounded_ceiling,
        default=DEFAULT_NONE_CEILING_MS,
    )
    return parser


def _technical_payload(i: int) -> dict:
    event = TechnicalEvent(
        event_type="flow.task.started",
        timestamp="2026-08-29T12:00:00+00:00",
        task_handle=f"task-{i:04d}",
        execution_id=f"exec-{i:04d}",
        tool="run_combo",
    )
    return build_technical_payload(event, level=LEVEL_TECHNICAL)


def measure_enqueue(config: BenchmarkConfig, state_dir: Path) -> dict[str, float]:
    emitter = ClioEmitter(state_dir=state_dir)
    payload = _technical_payload(0)
    for _ in range(config.warmups):
        emitter._producer.enqueue(payload)
    samples_ms: list[float] = []
    for i in range(config.samples):
        payload = _technical_payload(i + 1)
        start = time.perf_counter_ns()
        emitter._producer.enqueue(payload)
        elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000
        samples_ms.append(elapsed_ms)
    emitter.shutdown()
    return {
        "samples": float(len(samples_ms)),
        "p50_ms": statistics.median(samples_ms),
        "p95_ms": percentile(samples_ms, 95),
        "max_ms": max(samples_ms),
    }


def measure_none(config: BenchmarkConfig, state_dir: Path) -> dict[str, float]:
    env = {"ATHENA_CLIO_LEVEL": LEVEL_NONE}
    assert resolve_level(env=env) == LEVEL_NONE
    producer = ClioProducer(level=LEVEL_NONE)
    payload = _technical_payload(0)
    for _ in range(config.warmups):
        producer.enqueue(payload)
    samples_ms: list[float] = []
    for _ in range(config.samples):
        start = time.perf_counter_ns()
        producer.enqueue(payload)
        elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000
        samples_ms.append(elapsed_ms)
    return {
        "samples": float(len(samples_ms)),
        "p50_ms": statistics.median(samples_ms),
        "p95_ms": percentile(samples_ms, 95),
        "max_ms": max(samples_ms),
        "none_bypass": float(producer.counters.none_bypass),
    }


def run_benchmark(config: BenchmarkConfig, *, state_dir: Path) -> dict[str, object]:
    enqueue_stats = measure_enqueue(config, state_dir)
    none_stats = measure_none(config, state_dir / "none")
    report: dict[str, object] = {
        "schema": "athena.clio-benchmark",
        "version": "1.0",
        "samples": config.samples,
        "warmups": config.warmups,
        "enqueue": enqueue_stats,
        "none": none_stats,
    }
    if config.guardrail:
        report["guardrail"] = {
            "enqueue_pass": enqueue_stats["p95_ms"] <= config.enqueue_ceiling_ms,
            "none_pass": none_stats["p95_ms"] <= config.none_ceiling_ms,
        }
    return report


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = BenchmarkConfig(
        samples=args.samples,
        warmups=args.warmups,
        guardrail=args.guardrail,
        enqueue_ceiling_ms=args.enqueue_ceiling_ms,
        none_ceiling_ms=args.none_ceiling_ms,
    )
    state_root = Path(os.environ.get("ATHENA_CLIO_BENCH_DIR", "/tmp/athena-clio-bench"))
    state_root.mkdir(parents=True, exist_ok=True)
    report = run_benchmark(config, state_dir=state_root / "active")
    print(json.dumps(report, indent=2, sort_keys=True))
    if config.guardrail:
        guard = report.get("guardrail", {})
        if not guard.get("enqueue_pass") or not guard.get("none_pass"):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
