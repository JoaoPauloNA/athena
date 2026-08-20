"""Gate determinístico da integração do router com a fachada Aegis.

Excluído do fast gate por design; execute sob demanda, pois a comparação N=3 é cara.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_COMMIT = "6ceeee2"
REPETITIONS = 3
REPORT_PATH = Path("/tmp/compare_aegis.md")
SCENARIOS = ("confirmed_termination", "termination_unconfirmed")


def _result_state(exc: BaseException | None, result: Any | None) -> str | None:
    observed = result
    if exc is not None:
        observed = getattr(exc, "last_result", None)
    state = getattr(observed, "state", None)
    return getattr(state, "value", None)


def _worker(core: str, repetition: int) -> dict[str, Any]:
    import athena
    from athena.bridge import RunRequest, RunResult
    from athena.execution import ExecutionState
    from athena.lease import DirectoryLeaseManager
    from athena.profiles import FailureCondition, ServiceProfile
    from athena.router import ComboAttempt, ComboError, ComboRequest, ComboRouter

    class FakeBridge:
        def __init__(self, states: tuple[ExecutionState, ...]) -> None:
            self._states = iter(states)
            self.providers: list[str] = []

        def run(
            self,
            request: RunRequest,
            execution: Any,
            lease: Any,
            *,
            control: Any = None,
        ) -> RunResult:
            del control
            workspace = lease.acquire(
                request.cwd,
                execution.execution_id,
                execution.attempt_id,
                timeout=request.lease_timeout_s,
            )
            self.providers.append(execution.provider)
            state = next(self._states)
            if state is ExecutionState.TIMED_OUT:
                execution.transition(state)
            else:
                execution.transition(ExecutionState.STARTING)
                execution.transition(ExecutionState.RUNNING)
                execution.transition(state)
            lease.release(workspace, execution.execution_id, execution.attempt_id)
            return RunResult(
                command=tuple(request.command),
                cwd=Path(workspace),
                state=state,
                exit_code=0 if state is ExecutionState.COMPLETED else 1,
                stdout="ok" if state is ExecutionState.COMPLETED else "",
                stderr="",
                duration_s=0.0,
                error=None if state is ExecutionState.COMPLETED else state.value,
            )

    confirmed_state = {
        FailureCondition.TIMEOUT: ExecutionState.TIMED_OUT,
        FailureCondition.CANCELLATION: ExecutionState.CANCELLED,
    }
    workspace = Path(tempfile.gettempdir()) / "athena_aegis_fake_workspace"
    records: list[dict[str, Any]] = []
    for profile in ServiceProfile:
        for condition in FailureCondition:
            for scenario in SCENARIOS:
                first_state = (
                    confirmed_state.get(condition, ExecutionState.FAILED)
                    if scenario == "confirmed_termination"
                    else ExecutionState.TERMINATION_UNCONFIRMED
                )
                bridge = FakeBridge((first_state, ExecutionState.COMPLETED))
                combo = ComboRequest(
                    attempts=(
                        ComboAttempt(
                            "primary",
                            RunRequest(("fake-primary",), workspace),
                            failure_condition=condition,
                        ),
                        ComboAttempt(
                            "fallback",
                            RunRequest(("fake-fallback",), workspace),
                        ),
                    ),
                    profile=profile,
                    execution_id=(
                        f"aegis-gate-{core}-{repetition}-{profile.value}-"
                        f"{condition.value}-{scenario}"
                    ),
                )
                result = None
                raised: ComboError | None = None
                try:
                    result = ComboRouter(
                        bridge, DirectoryLeaseManager()
                    ).run(combo)
                except ComboError as exc:
                    raised = exc
                records.append(
                    {
                        "core": core,
                        "repetition": repetition,
                        "profile": profile.value,
                        "condition": condition.value,
                        "scenario": scenario,
                        "fallback": len(bridge.providers) == 2,
                        "exception": type(raised).__name__ if raised else None,
                        "final_state": _result_state(raised, result),
                    }
                )
    return {
        "core": core,
        "repetition": repetition,
        "athena_file": str(Path(athena.__file__).resolve()),
        "records": records,
    }


def _worker_process(core: str, root: Path, repetition: int) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        (
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            "--core",
            core,
            "--repetition",
            str(repetition),
        ),
        cwd=Path(tempfile.gettempdir()),
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30.0,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip()[-2000:]
        raise RuntimeError(f"worker {core}/{repetition} falhou: {detail}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"worker {core}/{repetition} retornou JSON inválido"
        ) from exc


def _make_read_only(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file():
            path.chmod(path.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def _add_baseline_worktree() -> tuple[Path, Path, Path]:
    temp_root = Path(tempfile.mkdtemp(prefix="athena_aegis_gate_"))
    controller = temp_root / "baseline.git"
    root = temp_root / "athena_baseline_6ceeee2"
    cloned = subprocess.run(
        ("git", "clone", "--bare", "--shared", str(REPO_ROOT), str(controller)),
        cwd=Path(tempfile.gettempdir()),
        check=False,
        capture_output=True,
        text=True,
    )
    if cloned.returncode != 0:
        shutil.rmtree(temp_root)
        raise RuntimeError("não foi possível criar o controlador baseline: " + cloned.stderr)
    completed = subprocess.run(
        (
            "git",
            f"--git-dir={controller}",
            "worktree",
            "add",
            "--detach",
            str(root),
            BASELINE_COMMIT,
        ),
        cwd=temp_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        shutil.rmtree(temp_root)
        raise RuntimeError("não foi possível criar o worktree baseline: " + completed.stderr)
    _make_read_only(root)
    return root, controller, temp_root


def _remove_baseline_worktree(root: Path, controller: Path, temp_root: Path) -> None:
    completed = subprocess.run(
        (
            "git",
            f"--git-dir={controller}",
            "worktree",
            "remove",
            "--force",
            str(root),
        ),
        cwd=temp_root,
        check=False,
        capture_output=True,
        text=True,
    )
    shutil.rmtree(temp_root)
    if completed.returncode != 0:
        raise RuntimeError("não foi possível remover o worktree baseline: " + completed.stderr)
    if root.exists() or controller.exists() or temp_root.exists():
        raise RuntimeError(f"worktree baseline deixou resíduo em {root}")


def _verify_clean_worktree() -> None:
    completed = subprocess.run(
        ("git", "status", "--porcelain"),
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError("não foi possível verificar se a árvore de trabalho está limpa")
    if completed.stdout:
        raise RuntimeError("a árvore de trabalho deve estar limpa antes da comparação")


def _observation(record: dict[str, Any]) -> tuple[Any, ...]:
    return (
        record["fallback"],
        record["exception"],
        record["final_state"],
    )


def _cell_key(record: dict[str, Any]) -> tuple[str, str, str]:
    return (record["profile"], record["condition"], record["scenario"])


def _analyse(runs: list[dict[str, Any]]) -> dict[str, Any]:
    indexed: dict[tuple[str, int, str, str, str], dict[str, Any]] = {}
    all_records: list[dict[str, Any]] = []
    for run in runs:
        for record in run["records"]:
            key = (record["core"], record["repetition"], *_cell_key(record))
            if key in indexed:
                raise RuntimeError(f"registro duplicado: {key}")
            indexed[key] = record
            all_records.append(record)

    cells = sorted({_cell_key(record) for record in all_records})
    classifications: dict[tuple[str, str, str], str] = {}
    mismatch_counts: dict[tuple[str, str, str], int] = {}
    for cell in cells:
        mismatches = 0
        for repetition in range(1, REPETITIONS + 1):
            head = indexed[("head", repetition, *cell)]
            baseline = indexed[("baseline", repetition, *cell)]
            mismatches += _observation(head) != _observation(baseline)
        mismatch_counts[cell] = mismatches
        if mismatches == 0:
            classifications[cell] = "identical"
        elif mismatches == REPETITIONS:
            classifications[cell] = "consistent_divergence"
        else:
            classifications[cell] = "noise"

    counts = Counter(classifications.values())
    expected_records = len(cells) * REPETITIONS * 2
    if len(cells) != 90 or len(all_records) != expected_records:
        raise RuntimeError(
            f"matriz incompleta: {len(cells)} células, {len(all_records)} execuções"
        )
    return {
        "records": sorted(
            all_records,
            key=lambda item: (
                item["repetition"],
                item["profile"],
                item["condition"],
                item["scenario"],
                item["core"],
            ),
        ),
        "cells": cells,
        "classifications": classifications,
        "mismatch_counts": mismatch_counts,
        "counts": counts,
    }


def _distribution(records: list[dict[str, Any]], core: str) -> Counter[tuple[Any, ...]]:
    return Counter(
        _observation(record) for record in records if record["core"] == core
    )


def _write_report(
    runs: list[dict[str, Any]],
    analysis: dict[str, Any],
    baseline_root: Path,
    elapsed_s: float,
) -> None:
    files = {run["core"]: run["athena_file"] for run in runs}
    counts: Counter[str] = analysis["counts"]
    records: list[dict[str, Any]] = analysis["records"]
    failed = counts["consistent_divergence"] > 0
    lines = [
        "# Gate comparativo da integração Aegis",
        "",
        "## Isolamento",
        "",
        f"- HEAD; `athena.__file__`: `{files['head']}`",
        f"- Baseline: `{BASELINE_COMMIT}`; `athena.__file__`: `{files['baseline']}`",
        f"- Worktree temporário somente-leitura: `{baseline_root.resolve()}`",
        "- Worktree removido ao final: sim (sem resíduo)",
        f"- Repetições: {REPETITIONS}; duração: {elapsed_s:.3f} s",
        "",
        "## Distribuição antes do veredito",
        "",
        "| Métrica | Contagem |",
        "|---|---:|",
        f"| Células totais (9 × 5 × 2) | {len(analysis['cells'])} |",
        f"| Células idênticas em 3/3 | {counts['identical']} |",
        f"| Divergências consistentes em 3/3 | {counts['consistent_divergence']} |",
        f"| Células ruidosas (divergência em 1–2/3) | {counts['noise']} |",
        f"| Execuções registradas (2 núcleos × 3 repetições) | {len(records)} |",
        "",
        "### Distribuição dos resultados observados",
        "",
        "| Núcleo | Fallback | Exceção | Estado final | Contagem |",
        "|---|---|---|---|---:|",
    ]
    for core in ("baseline", "head"):
        for observation, count in sorted(
            _distribution(records, core).items(), key=lambda item: str(item[0])
        ):
            fallback, exception, final_state = observation
            lines.append(
                f"| {core} | {'sim' if fallback else 'não'} | "
                f"{exception or '—'} | {final_state or '—'} | {count} |"
            )
    lines.extend(
        (
            "",
            "## Veredito",
            "",
            (
                "**FALHA** — ao menos uma célula divergiu consistentemente nas três "
                "repetições."
                if failed
                else "**OK** — nenhuma célula divergiu consistentemente nas três "
                "repetições; outliers isolados, se houvesse, seriam apenas ruído."
            ),
            "",
            "## Registro por execução",
            "",
            "| Rep. | Núcleo | Perfil | Condição | Cenário | Fallback | Exceção | Estado final |",
            "|---:|---|---|---|---|---|---|---|",
        )
    )
    for record in records:
        lines.append(
            f"| {record['repetition']} | {record['core']} | {record['profile']} | "
            f"{record['condition']} | {record['scenario']} | "
            f"{'sim' if record['fallback'] else 'não'} | "
            f"{record['exception'] or '—'} | {record['final_state'] or '—'} |"
        )
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _main() -> int:
    started = time.monotonic()
    _verify_clean_worktree()
    baseline_root, controller, temp_root = _add_baseline_worktree()
    runs: list[dict[str, Any]] = []
    removed = False
    try:
        for repetition in range(1, REPETITIONS + 1):
            runs.append(_worker_process("baseline", baseline_root, repetition))
            runs.append(_worker_process("head", REPO_ROOT, repetition))
        baseline_file = Path(runs[0]["athena_file"])
        head_file = Path(runs[1]["athena_file"])
        if baseline_root.resolve() not in baseline_file.parents:
            raise RuntimeError(f"baseline importou Athena fora do worktree: {baseline_file}")
        if REPO_ROOT.resolve() not in head_file.parents:
            raise RuntimeError(f"HEAD importou Athena fora do repositório: {head_file}")
        analysis = _analyse(runs)
    finally:
        _remove_baseline_worktree(baseline_root, controller, temp_root)
        removed = True
    if not removed:
        raise RuntimeError("worktree baseline não foi removido")
    _write_report(runs, analysis, baseline_root, time.monotonic() - started)
    counts = analysis["counts"]
    print(
        f"cells={len(analysis['cells'])} identical={counts['identical']} "
        f"divergent={counts['consistent_divergence']} noise={counts['noise']} "
        f"report={REPORT_PATH}"
    )
    return int(counts["consistent_divergence"] > 0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--core", choices=("baseline", "head"))
    parser.add_argument("--repetition", type=int)
    args = parser.parse_args()
    if args.worker:
        if args.core is None or args.repetition is None:
            parser.error("--worker exige --core e --repetition")
        print(json.dumps(_worker(args.core, args.repetition), sort_keys=True))
        return 0
    return _main()


if __name__ == "__main__":
    raise SystemExit(main())
