"""Benchmark OLIMPO O-2 — leitura local e ausência de overhead no startup MCP."""

from __future__ import annotations

import argparse
import importlib
import json
import math
import statistics
import sys
import time
import urllib.request
from dataclasses import dataclass

from athena.olimpo import (
    LOOPBACK_HOST,
    ROUTE_HEALTH,
    CompositionSources,
    OlimpoHttpServer,
    compose_dependencies,
    is_port_open,
)

MIN_SAMPLES = 5
MAX_SAMPLES = 1_000
MIN_WARMUPS = 1
MAX_WARMUPS = 100
PROBE_PORT = 17845


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    samples: int
    warmups: int


def _bounded_int(value: str, *, minimum: int, maximum: int, name: str) -> int:
    parsed = int(value)
    if not minimum <= parsed <= maximum:
        raise argparse.ArgumentTypeError(
            f"{name} must be between {minimum} and {maximum}"
        )
    return parsed


def percentile(values: list[float], pct: float) -> float:
    if not values:
        raise ValueError("values must not be empty")
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def measure_import_overhead() -> dict[str, float]:
    if "athena.olimpo" in sys.modules:
        del sys.modules["athena.olimpo"]
    start = time.perf_counter_ns()
    importlib.import_module("athena.olimpo")
    olimpo_ms = (time.perf_counter_ns() - start) / 1_000_000

    if "athena.mcp_runtime" in sys.modules:
        del sys.modules["athena.mcp_runtime"]
    start = time.perf_counter_ns()
    importlib.import_module("athena.mcp_runtime")
    runtime_ms = (time.perf_counter_ns() - start) / 1_000_000

    return {
        "olimpo_import_ms": olimpo_ms,
        "mcp_runtime_import_ms": runtime_ms,
        "olimpo_port_open": float(is_port_open(LOOPBACK_HOST, PROBE_PORT)),
    }


def measure_health_read(config: BenchmarkConfig) -> dict[str, float]:
    deps = compose_dependencies(
        CompositionSources(
            package_version="0.2.0",
            csrf_token="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        )
    )
    server = OlimpoHttpServer(deps, port=0)
    server.start()
    url = f"{server.base_url}{ROUTE_HEALTH}"
    try:
        for _ in range(config.warmups):
            with urllib.request.urlopen(url, timeout=2) as response:
                response.read()
        samples_ms: list[float] = []
        for _ in range(config.samples):
            start = time.perf_counter_ns()
            with urllib.request.urlopen(url, timeout=2) as response:
                response.read()
            samples_ms.append((time.perf_counter_ns() - start) / 1_000_000)
    finally:
        server.shutdown()
    return {
        "samples": float(len(samples_ms)),
        "p50_ms": statistics.median(samples_ms),
        "p95_ms": percentile(samples_ms, 95),
        "max_ms": max(samples_ms),
    }


def run_benchmark(config: BenchmarkConfig) -> dict[str, object]:
    return {
        "schema": "athena.olimpo-benchmark",
        "version": "1.0",
        "samples": config.samples,
        "warmups": config.warmups,
        "import_overhead": measure_import_overhead(),
        "health_read": measure_health_read(config),
        "notes": {
            "slo": "none — characterization only",
            "olimpo_autostart": False,
        },
    }


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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = BenchmarkConfig(samples=args.samples, warmups=args.warmups)
    report = run_benchmark(config)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
