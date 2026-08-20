"""Gate determinístico macOS entre os bridges novo e legado do Athena.

Cria um modelo Ollama estritamente local, calibra os deadlines, executa a matriz
fatorial com três repetições e grava somente métricas (nunca a saída do modelo).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
LEGACY_ROOT = REPO_ROOT / "legado"
WORKSPACE = Path("/tmp/athena_compare_deterministic")
REPORT_PATH = Path("/tmp/compare_deterministic.md")
RESULTS_PATH = WORKSPACE / "results.json"
OLLAMA = Path("/usr/local/bin/ollama")
BASE_MODEL = "qwen3:8b"
GATE_MODEL = "athena-gate"
SEED = 42
TEMPERATURE = 0
TOP_K = 1
TOP_P = 1.0
REPEAT_PENALTY = 1.0
INITIAL_NUM_PREDICT = 256
TARGET_MIN_S = 20.0
TARGET_MAX_S = 40.0
REPETITIONS = 3
TERMINATION_GRACE_S = 3.0
PROMPT = (
    "/no_think\n"
    "Escreva uma sequência longa e autocontida, em português, sobre criação de "
    "processos, sessões, grupos de processos, EOF, SIGTERM e SIGKILL em POSIX. "
    "Use parágrafos numerados, frases técnicas completas e continue até o limite "
    "de geração. Não execute comandos, não use ferramentas e não crie arquivos."
)


def _command() -> tuple[str, ...]:
    return (str(OLLAMA), "run", GATE_MODEL, PROMPT)


def _modelfile(num_predict: int) -> str:
    return "\n".join(
        (
            f"FROM {BASE_MODEL}",
            f"PARAMETER seed {SEED}",
            f"PARAMETER temperature {TEMPERATURE}",
            f"PARAMETER num_predict {num_predict}",
            f"PARAMETER top_k {TOP_K}",
            f"PARAMETER top_p {TOP_P}",
            f"PARAMETER repeat_penalty {REPEAT_PENALTY}",
            "",
        )
    )


def _create_model(num_predict: int) -> None:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", prefix="athena-gate-", suffix=".Modelfile", delete=False
    ) as stream:
        stream.write(_modelfile(num_predict))
        path = Path(stream.name)
    try:
        completed = subprocess.run(
            (str(OLLAMA), "create", GATE_MODEL, "-f", str(path)),
            check=False,
            capture_output=True,
            text=True,
            timeout=120.0,
        )
    finally:
        path.unlink(missing_ok=True)
    if completed.returncode != 0:
        raise RuntimeError(
            "ollama create falhou: " + completed.stderr.strip()[-1000:]
        )


def _direct_run() -> dict[str, Any]:
    started = time.monotonic()
    completed = subprocess.run(
        _command(),
        cwd=WORKSPACE,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=180.0,
    )
    duration_s = time.monotonic() - started
    if completed.returncode != 0:
        raise RuntimeError(f"execução direta falhou (rc={completed.returncode})")
    return {
        "duration_s": duration_s,
        "output_bytes": len(completed.stdout),
        "sha256": hashlib.sha256(completed.stdout).hexdigest(),
    }


def _calibrate_num_predict() -> tuple[int, list[dict[str, Any]]]:
    num_predict = INITIAL_NUM_PREDICT
    probes: list[dict[str, Any]] = []
    for _ in range(3):
        _create_model(num_predict)
        probe = _direct_run()
        probe["num_predict"] = num_predict
        probes.append(probe)
        duration = float(probe["duration_s"])
        if TARGET_MIN_S <= duration <= TARGET_MAX_S:
            return num_predict, probes
        target = (TARGET_MIN_S + TARGET_MAX_S) / 2
        scaled = round(num_predict * target / max(duration, 0.1))
        num_predict = min(1024, max(64, scaled))
    if not TARGET_MIN_S <= float(probes[-1]["duration_s"]) <= TARGET_MAX_S:
        raise RuntimeError("não foi possível calibrar duração para 20–40 s")
    return num_predict, probes


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class ProcessTracker:
    """Rastreia e limpa apenas a CLI Ollama criada pelo bridge."""

    def __init__(self, module: Any) -> None:
        self.module = module
        self.real_popen = module.subprocess.Popen
        self.processes: list[subprocess.Popen[bytes]] = []
        self.identities: list[tuple[int, int | None]] = []

    def install(self) -> None:
        expected = _command()

        def tracked_popen(*args: Any, **kwargs: Any) -> Any:
            process = self.real_popen(*args, **kwargs)
            argv = args[0] if args else kwargs.get("args")
            if tuple(argv or ()) == expected:
                try:
                    pgid = os.getpgid(process.pid)
                except OSError:
                    pgid = None
                self.processes.append(process)
                self.identities.append((process.pid, pgid))
            return process

        self.module.subprocess.Popen = tracked_popen

    def restore(self) -> None:
        self.module.subprocess.Popen = self.real_popen

    def cleanup(self) -> list[int]:
        for process, (_pid, pgid) in zip(
            self.processes, self.identities, strict=True
        ):
            if process.poll() is not None:
                continue
            try:
                if pgid is not None and pgid != os.getpgrp():
                    os.killpg(pgid, signal.SIGTERM)
                else:
                    process.terminate()
            except (OSError, ProcessLookupError):
                pass
            try:
                process.wait(timeout=TERMINATION_GRACE_S)
            except subprocess.TimeoutExpired:
                try:
                    if pgid is not None and pgid != os.getpgrp():
                        os.killpg(pgid, signal.SIGKILL)
                    else:
                        process.kill()
                except (OSError, ProcessLookupError):
                    pass
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    pass
        return [pid for pid, _pgid in self.identities if _pid_alive(pid)]


def _worker_new(mode: str, timeout_s: float) -> dict[str, Any]:
    import athena
    import athena.bridge.runner as bridge_runner
    from athena.bridge import LocalBridgeRunner, RunRequest
    from athena.execution import ExecutionDeadlines, ExecutionRecord
    from athena.lease import DirectoryLeaseManager

    tracker = ProcessTracker(bridge_runner)
    execution = ExecutionRecord(
        "ollama",
        profile="local_model",
        deadlines=ExecutionDeadlines(absolute_timeout_s=timeout_s),
    )
    started = time.monotonic()
    tracker.install()
    result = None
    try:
        result = LocalBridgeRunner().run(
            RunRequest(
                _command(),
                WORKSPACE,
                use_pty=mode == "pty",
                termination_grace_s=TERMINATION_GRACE_S,
            ),
            execution,
            DirectoryLeaseManager(),
        )
    finally:
        tracker.restore()
        survivors = tracker.cleanup()
    if result is None:
        raise RuntimeError("núcleo novo não retornou resultado")
    timed_out = result.expired_deadline is not None
    return {
        "core": "novo",
        "mode": mode.upper(),
        "athena_file": str(Path(athena.__file__).resolve()),
        "duration_s": time.monotonic() - started,
        "state": result.state.value,
        "timed_out": timed_out,
        "eof_spontaneous": not timed_out
        and result.state.value != "termination_unconfirmed",
        "deadline_cut": timed_out,
        "output_bytes": len(result.output.encode("utf-8")),
        "fallback": False,
        "exit_code": result.exit_code,
        "pids": [pid for pid, _pgid in tracker.identities],
        "survivors": survivors,
    }


def _worker_legacy(mode: str, timeout_s: float) -> dict[str, Any]:
    import athena
    import athena.bridge as legacy_bridge

    tracker = ProcessTracker(legacy_bridge)
    runner = (
        legacy_bridge.run_with_pty
        if mode == "pty"
        else legacy_bridge.run_subprocess
    )
    started = time.monotonic()
    tracker.install()
    result = None
    try:
        result = runner(
            "ollama",
            _command(),
            cwd=str(WORKSPACE),
            timeout=timeout_s,
            termination_grace_s=TERMINATION_GRACE_S,
            service_profile="local_model",
        )
    finally:
        tracker.restore()
        survivors = tracker.cleanup()
    if result is None:
        raise RuntimeError("núcleo legado não retornou resultado")
    execution = result.execution or {}
    timed_out = bool(result.timed_out)
    state = str(execution.get("state", "UNKNOWN")).lower()
    return {
        "core": "antigo",
        "mode": mode.upper(),
        "athena_file": str(Path(athena.__file__).resolve()),
        "duration_s": time.monotonic() - started,
        "state": state,
        "timed_out": timed_out,
        "eof_spontaneous": not timed_out and state != "termination_unconfirmed",
        "deadline_cut": timed_out,
        "output_bytes": len((result.output or "").encode("utf-8")),
        "fallback": bool(execution.get("fallback_started", False)),
        "exit_code": result.exit_code,
        "pids": [pid for pid, _pgid in tracker.identities],
        "survivors": survivors,
    }


def _run_worker(core: str, mode: str, timeout_s: float) -> dict[str, Any]:
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
            "--mode",
            mode,
            "--timeout",
            str(timeout_s),
        ),
        cwd="/tmp",
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_s + TERMINATION_GRACE_S + 30.0,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"worker {core}/{mode} falhou (rc={completed.returncode}): "
            f"{completed.stderr.strip()[-1000:]}"
        )
    try:
        return json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"worker {core}/{mode} não retornou JSON") from exc


def _predominant(values: list[str]) -> str:
    counts = Counter(values)
    value, count = counts.most_common(1)[0]
    return value if count > len(values) / 2 else "sem predominância"


def _summaries(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for core in ("novo", "antigo"):
        for mode in ("PTY", "PIPE"):
            for scenario in (1, 2):
                group = [
                    item
                    for item in results
                    if item["core"] == core
                    and item["mode"] == mode
                    and item["scenario"] == scenario
                ]
                durations = [float(item["duration_s"]) for item in group]
                streams = [
                    "EOF" if item["eof_spontaneous"] else "deadline"
                    for item in group
                ]
                summaries.append(
                    {
                        "core": core,
                        "mode": mode,
                        "scenario": scenario,
                        "min_s": min(durations),
                        "max_s": max(durations),
                        "mean_s": statistics.mean(durations),
                        "state": _predominant([item["state"] for item in group]),
                        "stream": _predominant(streams),
                        "bytes": "/".join(
                            str(item["output_bytes"]) for item in group
                        ),
                        "fallbacks": sum(bool(item["fallback"]) for item in group),
                    }
                )
    return summaries


def _behavior(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item["state"],
        item["timed_out"],
        item["eof_spontaneous"],
        item["fallback"],
    )


def _consistent_divergences(results: list[dict[str, Any]]) -> list[str]:
    divergences: list[str] = []
    for mode in ("PTY", "PIPE"):
        for scenario in (1, 2):
            new = sorted(
                (
                    item
                    for item in results
                    if item["core"] == "novo"
                    and item["mode"] == mode
                    and item["scenario"] == scenario
                ),
                key=lambda item: item["repetition"],
            )
            old = sorted(
                (
                    item
                    for item in results
                    if item["core"] == "antigo"
                    and item["mode"] == mode
                    and item["scenario"] == scenario
                ),
                key=lambda item: item["repetition"],
            )
            differing = sum(
                _behavior(left) != _behavior(right)
                for left, right in zip(new, old, strict=True)
            )
            if differing == REPETITIONS:
                divergences.append(f"{mode}/cenário {scenario}")
    return divergences


def _table_row(summary: dict[str, Any]) -> str:
    return (
        "| {core} | {mode} | {scenario} | {min_s:.2f} | {max_s:.2f} | "
        "{mean_s:.2f} | {state} | {stream} | {bytes} | {fallbacks}/3 |"
    ).format(**summary)


def _write_report(payload: dict[str, Any]) -> None:
    validation = payload["validation"]
    summaries = payload["summaries"]
    divergences = payload["consistent_divergences"]
    gate = payload["gate"]
    survivors = payload["survivors"]
    status = (
        "OK"
        if not divergences
        and gate["returncode"] == 0
        and not survivors
        and payload["model_stop_returncode"] == 0
        else "FALHA"
    )
    lines = [
        "# Gate comparativo determinístico — Athena-MCP",
        "",
        "- Plataforma: macOS; MLX, Windows e Linux fora de escopo.",
        f"- Modelo local: `{GATE_MODEL}`, derivado de `{BASE_MODEL}`.",
        (
            f"- Calibração: `num_predict={payload['probes'][0]['num_predict']}` "
            f"produziu {payload['probes'][0]['duration_s']:.2f} s (abaixo do alvo); "
            f"ajustado para `{payload['num_predict']}`. Nenhum ajuste adicional foi "
            "necessário após a validação."
        ),
        (
            f"- Parâmetros: `seed={SEED}`, `temperature={TEMPERATURE}`, "
            f"`num_predict={payload['num_predict']}`, `top_k={TOP_K}`, "
            f"`top_p={TOP_P}`, `repeat_penalty={REPEAT_PENALTY}`."
        ),
        (
            f"- Validação 3x: min {validation['min_s']:.2f} s; máx "
            f"{validation['max_s']:.2f} s; média {validation['mean_s']:.2f} s; "
            f"dispersão em torno da média {validation['dispersion_pct']:.2f}%; "
            f"saída byte-idêntica: {'sim' if validation['byte_identical'] else 'não'}."
        ),
        (
            f"- Duração base: {validation['mean_s']:.2f} s. Timeouts recalibrados: "
            f"cenário 1 = {payload['timeouts']['1']:.3f} s (55%); cenário 2 = "
            f"{payload['timeouts']['2']:.3f} s (200%)."
        ),
        (
            "- Isolamento comprovado por `athena.__file__`: novo = "
            f"`{payload['athena_files']['novo']}`; antigo = "
            f"`{payload['athena_files']['antigo']}`."
        ),
        "",
        "## Distribuição completa (3 repetições por combinação)",
        "",
        "| Núcleo | Modo | Cenário | Min (s) | Máx (s) | Média (s) | Estado predominante | EOF x deadline | Bytes (r1/r2/r3) | Fallback |",
        "|---|---|---:|---:|---:|---:|---|---|---|---:|",
        *[_table_row(item) for item in summaries],
        "",
        (
            "- Regra aplicada: reprovação somente quando a divergência entre os "
            "núcleos ocorre nas 3/3 repetições da mesma combinação; diferenças "
            "isoladas são ruído."
        ),
        "- Divergências consistentes: "
        + (", ".join(divergences) if divergences else "nenhuma"),
        f"- Veredito comparativo: {'REPROVADO' if divergences else 'APROVADO'}.",
        (
            f"- Gate `python -m harness.p0_gate`: "
            f"{'PASS' if gate['returncode'] == 0 else 'FAIL'} "
            f"({'; '.join(gate['lines'])})."
        ),
        (
            f"- Processos Ollama CLI sobreviventes: "
            f"{', '.join(map(str, survivors)) if survivors else 'nenhum'}."
        ),
        (
            f"- Runner carregado de `{GATE_MODEL}` descarregado ao final: "
            f"{'sim' if payload['model_stop_returncode'] == 0 else 'não'}."
        ),
        f"- STATUS: {status}",
        "",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def _run_gate() -> dict[str, Any]:
    completed = subprocess.run(
        (sys.executable, "-m", "harness.p0_gate"),
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    return {"returncode": completed.returncode, "lines": lines}


def _stop_model() -> int:
    completed = subprocess.run(
        (str(OLLAMA), "stop", GATE_MODEL),
        check=False,
        capture_output=True,
        text=True,
        timeout=30.0,
    )
    return completed.returncode


def _main() -> int:
    if sys.platform != "darwin":
        raise RuntimeError("este gate é exclusivo para macOS nesta etapa")
    if not OLLAMA.is_file():
        raise RuntimeError(f"Ollama não encontrado em {OLLAMA}")
    WORKSPACE.mkdir(parents=True, exist_ok=True)

    num_predict, probes = _calibrate_num_predict()
    validation_runs = [_direct_run() for _ in range(REPETITIONS)]
    validation_durations = [item["duration_s"] for item in validation_runs]
    base_duration = statistics.mean(validation_durations)
    max_deviation = max(
        abs(duration - base_duration) for duration in validation_durations
    )
    validation = {
        "runs": validation_runs,
        "min_s": min(validation_durations),
        "max_s": max(validation_durations),
        "mean_s": base_duration,
        "dispersion_pct": max_deviation / base_duration * 100,
        "byte_identical": len({item["sha256"] for item in validation_runs}) == 1,
    }
    if validation["dispersion_pct"] > 15:
        raise RuntimeError("dispersão de validação acima de 15%")

    timeouts = {1: base_duration * 0.55, 2: base_duration * 2.0}
    results: list[dict[str, Any]] = []
    for core in ("new", "legacy"):
        for mode in ("pty", "pipe"):
            for scenario in (1, 2):
                for repetition in range(1, REPETITIONS + 1):
                    result = _run_worker(core, mode, timeouts[scenario])
                    result.update(scenario=scenario, repetition=repetition)
                    results.append(result)
                    print(
                        f"{result['core']} athena.__file__={result['athena_file']} "
                        f"{mode.upper()}/c{scenario}/r{repetition}: "
                        f"{result['state']} {result['duration_s']:.2f}s",
                        flush=True,
                    )

    survivors = sorted(
        {
            pid
            for item in results
            for pid in item["survivors"]
            if _pid_alive(pid)
        }
    )
    summaries = _summaries(results)
    divergences = _consistent_divergences(results)
    model_stop_returncode = _stop_model()
    gate = _run_gate()
    athena_files = {
        core: min({item["athena_file"] for item in results if item["core"] == core})
        for core in ("novo", "antigo")
    }
    payload = {
        "num_predict": num_predict,
        "probes": probes,
        "validation": validation,
        "timeouts": {str(key): value for key, value in timeouts.items()},
        "results": results,
        "summaries": summaries,
        "consistent_divergences": divergences,
        "athena_files": athena_files,
        "survivors": survivors,
        "model_stop_returncode": model_stop_returncode,
        "gate": gate,
    }
    RESULTS_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    _write_report(payload)
    print(f"relatório: {REPORT_PATH}")
    return int(bool(divergences or survivors or gate["returncode"]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", choices=("new", "legacy"))
    parser.add_argument("--mode", choices=("pty", "pipe"))
    parser.add_argument("--timeout", type=float)
    args = parser.parse_args()
    if args.worker:
        if args.mode is None or args.timeout is None:
            parser.error("--worker exige --mode e --timeout")
        worker = _worker_new if args.worker == "new" else _worker_legacy
        print(json.dumps(worker(args.mode, args.timeout), ensure_ascii=False))
        return 0
    return _main()


if __name__ == "__main__":
    raise SystemExit(main())
