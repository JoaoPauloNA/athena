#!/usr/bin/env python3
"""Synthetic offline gate runner for A14/P0."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

RESULTS_DIR = Path("harness/results")
SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class StageSpec:
    id: str
    argv: tuple[str, ...]
    test_like: bool = False


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp_tag(now: datetime | None = None) -> str:
    now = now or _utc_now()
    return now.strftime("%Y%m%dT%H%M%SZ")


def _platform_family() -> str:
    return "windows" if sys.platform.startswith("win") else "posix"


def _support_classification() -> dict[str, str]:
    family = _platform_family()
    windows_status = "NOT_GUARANTEED"
    posix_status = "LOCAL_CONTROLLED_ONLY"
    return {
        "platform_family": family,
        "windows": windows_status,
        "posix": posix_status,
        "effective": windows_status if family == "windows" else posix_status,
    }


def _extract_test_count(output: str) -> int | None:
    match = re.search(r"collected\s+(\d+)\s+items", output)
    if match:
        return int(match.group(1))
    match = re.search(r"=+\s+([0-9]+)\s+passed", output)
    if match:
        return int(match.group(1))
    return None


def _build_stages() -> list[StageSpec]:
    py = sys.executable
    return [
        StageSpec(
            id="lint_ruff_synthetic_targets",
            argv=(
                py,
                "-m",
                "ruff",
                "check",
                "harness/p0_gate.py",
                "tests/test_execution_lifecycle.py",
                "tests/test_router.py",
                "tests/test_workspace_lease.py",
                "tests/test_provider_workspace_lease.py",
                "tests/test_mcp_server_entrypoint.py",
                "tests/test_execution_registry.py",
                "tests/test_ssh.py",
                "tests/test_service_profiles.py",
                "tests/test_reliability.py",
                "tests/test_dverify.py",
                "tests/test_dverify_paths.py",
                "tests/test_moiras_adapter.py",
                "tests/test_moiras_adapter_integration.py",
            ),
            test_like=False,
        ),
        StageSpec(
            id="tests_lifecycle_timeout_progress_process_tree",
            argv=(py, "-m", "pytest", "tests/test_execution_lifecycle.py"),
            test_like=True,
        ),
        StageSpec(
            id="tests_router_fallback",
            argv=(py, "-m", "pytest", "tests/test_router.py"),
            test_like=True,
        ),
        StageSpec(
            id="tests_workspace_lease",
            argv=(py, "-m", "pytest", "tests/test_workspace_lease.py"),
            test_like=True,
        ),
        StageSpec(
            id="tests_provider_workspace_lease",
            argv=(py, "-m", "pytest", "tests/test_provider_workspace_lease.py"),
            test_like=True,
        ),
        StageSpec(
            id="tests_mcp_registry_cancel_eof",
            argv=(
                py,
                "-m",
                "pytest",
                "tests/test_mcp_server_entrypoint.py",
                "tests/test_execution_registry.py",
            ),
            test_like=True,
        ),
        StageSpec(
            id="tests_ssh_builder",
            argv=(py, "-m", "pytest", "tests/test_ssh.py"),
            test_like=True,
        ),
        StageSpec(
            id="tests_service_profiles",
            argv=(py, "-m", "pytest", "tests/test_service_profiles.py"),
            test_like=True,
        ),
        StageSpec(
            id="tests_privacy_reliability",
            argv=(
                py,
                "-m",
                "pytest",
                "tests/test_reliability.py",
                "tests/test_dverify.py",
                "tests/test_dverify_paths.py",
            ),
            test_like=True,
        ),
        StageSpec(
            id="tests_optional_moiras_adapter",
            argv=(py, "-m", "pytest", "tests/test_moiras_adapter.py"),
            test_like=True,
        ),
    ]


def _run_stage(stage: StageSpec, env: dict[str, str]) -> dict:
    started = time.monotonic()
    proc = subprocess.run(
        stage.argv,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    duration_s = round(time.monotonic() - started, 3)
    merged = f"{proc.stdout}\n{proc.stderr}"
    payload: dict[str, object] = {
        "id": stage.id,
        "status": "passed" if proc.returncode == 0 else "failed",
        "exit_code": proc.returncode,
        "duration_s": duration_s,
    }
    if stage.test_like:
        count = _extract_test_count(merged)
        if count is not None:
            payload["test_count"] = count
    return payload


def _build_result(stages: list[dict], now: datetime | None = None) -> dict:
    now = now or _utc_now()
    status = "passed" if all(s["status"] == "passed" for s in stages) else "failed"
    return {
        "schema_version": SCHEMA_VERSION,
        "timestamp_utc": now.isoformat().replace("+00:00", "Z"),
        "platform_family": _platform_family(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "support_classification": _support_classification(),
        "overall_status": status,
        "stages": stages,
    }


def _default_output_path(now: datetime | None = None) -> Path:
    return RESULTS_DIR / f"p0-gate-{_timestamp_tag(now)}.json"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run synthetic offline A14/P0 gate")
    parser.add_argument(
        "--output",
        type=Path,
        help="Output JSON path (default: harness/results/p0-gate-<timestamp>.json)",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = args.output or _default_output_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    stages: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="athena_p0_gate_data_") as tmp_data_dir:
        env = os.environ.copy()
        env["ATHENA_DATA_DIR"] = tmp_data_dir
        env["ATHENA_SKIP_AUTODISCOVERY"] = "1"
        for stage in _build_stages():
            stages.append(_run_stage(stage, env))

    payload = _build_result(stages)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")

    if payload["overall_status"] == "passed":
        print(f"A14/P0 synthetic gate passed ({len(stages)}/{len(stages)} stages).")
        print(f"Result file: {output_path}")
        return 0

    failed = [s["id"] for s in stages if s["status"] != "passed"]
    print(
        "A14/P0 synthetic gate failed "
        f"({len(stages) - len(failed)}/{len(stages)} stages passed). "
        f"Failed stages: {', '.join(failed)}"
    )
    print(f"Result file: {output_path}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
