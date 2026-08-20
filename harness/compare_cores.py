"""Gate manual comparativo dos núcleos novo e legado com uma CLI real local."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
LEGACY_ROOT = REPO_ROOT / "legado"
REPORT_PATH = Path("/tmp/compare_report.md")
WORKSPACE = Path("/tmp/athena_compare_cores")
OLLAMA = Path("/usr/local/bin/ollama")
MODEL = "qwen3:8b"
MARKER = "ATHENA_COMPARE_CORES_GATE_20260820"
PROMPT = (
    f"Marcador de teste: {MARKER}. "
    "Explique em português, de forma técnica e autocontida, como um sistema "
    "operacional POSIX cria processos, sessões e grupos de processos e como aplica "
    "SIGTERM, período de graça e SIGKILL com confirmação de término. Produza "
    "exatamente 20 seções numeradas, cada uma com exatamente 3 frases completas e "
    "substanciais. Não use ferramentas, não execute comandos e não escreva arquivos."
)
COMMAND = (str(OLLAMA), "run", MODEL, PROMPT)


def _command_text() -> str:
    return shlex.join(COMMAND)


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _cleanup_cli_processes(identities: set[tuple[int, int]]) -> set[int]:
    for pid, pgid in identities:
        try:
            if pgid != pid or pgid == os.getpgrp():
                os.kill(pid, signal.SIGTERM)
            else:
                os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and any(
        _group_alive(pgid) for _pid, pgid in identities
    ):
        time.sleep(0.05)
    survivors = {(pid, pgid) for pid, pgid in identities if _group_alive(pgid)}
    for pid, pgid in survivors:
        try:
            if pgid != pid or pgid == os.getpgrp():
                os.kill(pid, signal.SIGKILL)
            else:
                os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and any(
        _group_alive(pgid) for _pid, pgid in survivors
    ):
        time.sleep(0.05)
    return {pid for pid, pgid in survivors if _group_alive(pgid)}


def _tracked_popen(module: Any) -> tuple[Any, list[tuple[int, int]]]:
    real_popen = module.subprocess.Popen
    identities: list[tuple[int, int]] = []

    def tracking_popen(*args: Any, **kwargs: Any) -> Any:
        process = real_popen(*args, **kwargs)
        argv = args[0] if args else kwargs.get("args")
        if tuple(argv or ()) == COMMAND:
            try:
                pgid = os.getpgid(process.pid)
            except OSError:
                pgid = process.pid
            identities.append((process.pid, pgid))
        return process

    module.subprocess.Popen = tracking_popen
    return real_popen, identities


def _worker_new(timeout_s: float) -> dict[str, Any]:
    import athena
    import athena.bridge.runner as bridge_runner
    from athena.bridge import LocalBridgeRunner, RunRequest
    from athena.execution import ExecutionDeadlines, ExecutionRecord
    from athena.lease import DirectoryLeaseManager

    started = time.monotonic()
    execution = ExecutionRecord(
        "ollama",
        profile="local_model",
        deadlines=ExecutionDeadlines(absolute_timeout_s=timeout_s),
    )
    real_popen, identities = _tracked_popen(bridge_runner)
    try:
        result = LocalBridgeRunner().run(
            RunRequest(COMMAND, WORKSPACE, termination_grace_s=3.0),
            execution,
            DirectoryLeaseManager(),
        )
    finally:
        bridge_runner.subprocess.Popen = real_popen
    state = result.state.value
    return {
        "core": "novo",
        "athena_file": str(Path(athena.__file__).resolve()),
        "duration_s": time.monotonic() - started,
        "state": state,
        "deadline_observed": result.expired_deadline is not None,
        "fallback": False,
        "fallback_improper": False,
        "termination_confirmed": state != "termination_unconfirmed",
        "exit_code": result.exit_code,
        "process_identities": identities,
    }


def _worker_legacy(timeout_s: float) -> dict[str, Any]:
    import athena
    import athena.bridge as legacy_bridge
    from athena.bridge import run_subprocess

    started = time.monotonic()
    real_popen, identities = _tracked_popen(legacy_bridge)
    try:
        result = run_subprocess(
            "ollama",
            COMMAND,
            cwd=str(WORKSPACE),
            timeout=timeout_s,
            termination_grace_s=3.0,
            service_profile="local_model",
        )
    finally:
        legacy_bridge.subprocess.Popen = real_popen
    execution = result.execution or {}
    fallback = bool(execution.get("fallback_started", False))
    process_created = bool(execution.get("process_created", False))
    direct_confirmed = bool(execution.get("direct_process_terminated_confirmed", False))
    pgid_present = execution.get("pgid") is not None
    tree_confirmed = bool(execution.get("process_tree_terminated_confirmed", False))
    termination_confirmed = not process_created or (
        direct_confirmed and (not pgid_present or tree_confirmed)
    )
    return {
        "core": "antigo",
        "athena_file": str(Path(athena.__file__).resolve()),
        "duration_s": time.monotonic() - started,
        "state": execution.get("state", "UNKNOWN"),
        "deadline_observed": bool(result.timed_out),
        "fallback": fallback,
        "fallback_improper": fallback and not termination_confirmed,
        "termination_confirmed": termination_confirmed,
        "exit_code": result.exit_code,
        "process_identities": identities,
    }


def _run_worker(core: str, timeout_s: float) -> dict[str, Any]:
    pythonpath = REPO_ROOT if core == "new" else LEGACY_ROOT
    env = os.environ.copy()
    env["PYTHONPATH"] = str(pythonpath)
    env["ATHENA_DATA_DIR"] = str(WORKSPACE / "legacy-data")
    env["ATHENA_LOGS_DIR"] = str(WORKSPACE / "legacy-data" / "logs")
    completed = subprocess.run(
        (
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            core,
            "--timeout",
            str(timeout_s),
        ),
        cwd="/tmp",
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=max(30.0, timeout_s + 20.0),
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"worker {core} falhou (rc={completed.returncode}): "
            f"{completed.stderr.strip()[-1000:]}"
        )
    try:
        return json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"worker {core} não retornou JSON válido") from exc


def _calibrate() -> tuple[float, tuple[int, int]]:
    started = time.monotonic()
    process = subprocess.Popen(
        COMMAND,
        cwd=WORKSPACE,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    identity = (process.pid, os.getpgid(process.pid))
    try:
        process.communicate()
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)
        raise
    duration = time.monotonic() - started
    if process.returncode != 0:
        raise RuntimeError(f"calibração do ollama falhou (rc={process.returncode})")
    return duration, identity


def _native_verdict(result: dict[str, Any], scenario: str) -> str:
    if scenario == "deadline":
        safe = result["deadline_observed"] and result["termination_confirmed"]
        return "deadline tratado; término confirmado" if safe else "deadline inseguro"
    return (
        "conclusão normal" if result["state"].lower() == "completed" else "não concluiu"
    )


def _write_report(
    expected_s: float,
    short_timeout_s: float,
    long_timeout_s: float,
    results: list[dict[str, Any]],
    survivors: set[int],
) -> None:
    scenarios = {"deadline": results[:2], "normal": results[2:]}
    same = {
        name: len({_native_verdict(item, name) for item in items}) == 1
        for name, items in scenarios.items()
    }
    lines = [
        "# Comparação dos núcleos Athena",
        "",
        f"- CLI: `{OLLAMA}` (modelo `{MODEL}`)",
        f"- Comando exato: `{_command_text()}`",
        f"- Duração esperada (calibração sem timeout): {expected_s:.2f} s",
        f"- Timeout do cenário 1: {short_timeout_s:.2f} s ({short_timeout_s / expected_s:.0%} da calibração)",
        f"- Timeout do cenário 2: {long_timeout_s:.2f} s ({long_timeout_s / expected_s:.0%} da calibração)",
        "",
        "| Núcleo | athena.__file__ | Cenário | Timeout (s) | Duração real (s) | Estado final | Fallback | Fallback indevido | Término confirmado |",
        "|---|---|---|---:|---:|---|---|---|---|",
    ]
    for index, result in enumerate(results):
        scenario = "1 — deadline" if index < 2 else "2 — normal"
        timeout = short_timeout_s if index < 2 else long_timeout_s
        lines.append(
            "| {core} | `{athena_file}` | {scenario} | {timeout:.2f} | "
            "{duration_s:.2f} | {state} | {fallback} | {improper} | {confirmed} |".format(
                core=result["core"],
                athena_file=result["athena_file"],
                scenario=scenario,
                timeout=timeout,
                duration_s=result["duration_s"],
                state=result["state"],
                fallback="sim" if result["fallback"] else "não",
                improper="sim" if result["fallback_improper"] else "não",
                confirmed="sim" if result["termination_confirmed"] else "não",
            )
        )
    deadline_states = " vs ".join(item["state"] for item in scenarios["deadline"])
    normal_states = " vs ".join(item["state"] for item in scenarios["normal"])
    lines.extend(
        [
            "",
            "## VEREDITO",
            "",
            f"- Cenário 1: {'MESMO veredito operacional' if same['deadline'] else 'DIVERGÊNCIA'}; estados nativos: {deadline_states}.",
            f"- Cenário 2: {'MESMO veredito' if same['normal'] else 'DIVERGÊNCIA'}; estados nativos: {normal_states}.",
            f"- Processos `ollama run` sobreviventes após limpeza: {len(survivors)}"
            + (
                f" (PIDs: {', '.join(map(str, sorted(survivors)))})"
                if survivors
                else "."
            ),
            "- Observação: este harness executa uma tentativa por núcleo; nenhum caminho de fallback foi iniciado.",
            "",
        ]
    )
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def _main() -> int:
    if not OLLAMA.is_file():
        raise RuntimeError(f"ollama não encontrado em {OLLAMA}")
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    identities: set[tuple[int, int]] = set()
    try:
        expected_s, calibration_identity = _calibrate()
        identities.add(calibration_identity)
        short_timeout_s = max(1.0, expected_s * 0.55)
        long_timeout_s = max(short_timeout_s + 1.0, expected_s * 2.0)
        for scenario, timeout_s in (
            ("deadline", short_timeout_s),
            ("normal", long_timeout_s),
        ):
            for core in ("new", "legacy"):
                result = _run_worker(core, timeout_s)
                identities.update(tuple(item) for item in result["process_identities"])
                result["scenario"] = scenario
                results.append(result)
                print(
                    f"{result['core']}: athena.__file__={result['athena_file']}",
                    flush=True,
                )
    finally:
        survivors = _cleanup_cli_processes(identities)
    if len(results) != 4:
        raise RuntimeError("execução comparativa não produziu quatro resultados")
    _write_report(expected_s, short_timeout_s, long_timeout_s, results, survivors)
    print(f"relatório: {REPORT_PATH}")
    return int(bool(survivors))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", choices=("new", "legacy"))
    parser.add_argument("--timeout", type=float)
    args = parser.parse_args()
    if args.worker:
        if args.timeout is None:
            parser.error("--timeout é obrigatório com --worker")
        result = (
            _worker_new(args.timeout)
            if args.worker == "new"
            else _worker_legacy(args.timeout)
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    return _main()


if __name__ == "__main__":
    raise SystemExit(main())
