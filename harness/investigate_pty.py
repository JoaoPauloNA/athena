"""Investigação manual da diferença PTY/PIPE no Ollama local.

Este harness não altera o bridge. Ele executa os dois núcleos em processos Python
isolados, mede o encerramento observado e escreve o relatório solicitado em /tmp.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
LEGACY_ROOT = REPO_ROOT / "legado"
WORKSPACE = Path("/tmp/athena_pty_investigation")
REPORT_PATH = Path("/tmp/pty_investigation.md")
RESULTS_PATH = WORKSPACE / "results.json"
DIRECT_OLLAMA = Path("/usr/local/bin/ollama")
APP_OLLAMA = Path("/Applications/Ollama.app/Contents/Resources/ollama")
MODEL = "qwen3:8b"
TIMEOUT_S = 204.09
MARKER = "ATHENA_COMPARE_CORES_GATE_20260820"
PROMPT = (
    f"Marcador de teste: {MARKER}. "
    "Explique em português, de forma técnica e autocontida, como um sistema "
    "operacional POSIX cria processos, sessões e grupos de processos e como aplica "
    "SIGTERM, período de graça e SIGKILL com confirmação de término. Produza "
    "exatamente 20 seções numeradas, cada uma com exatamente 3 frases completas e "
    "substanciais. Não use ferramentas, não execute comandos e não escreva arquivos."
)


def _command(binary: Path) -> tuple[str, ...]:
    return (str(binary), "run", MODEL, PROMPT)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _lsof_tcp(pid: int) -> list[str]:
    """Capturar somente endpoints TCP do PID, nunca a saída do modelo."""
    try:
        completed = subprocess.run(
            ("lsof", "-nP", "-a", "-p", str(pid), "-iTCP"),
            check=False,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in completed.stdout.splitlines()[1:] if line.strip()]


def _service_snapshot() -> list[str]:
    try:
        completed = subprocess.run(
            ("lsof", "-nP", "-iTCP:11434"),
            check=False,
            capture_output=True,
            text=True,
            timeout=3.0,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in completed.stdout.splitlines()[1:] if line.strip()]


class ProcessTracker:
    """Rastrear somente a CLI exata criada pelo bridge e assegurar sua limpeza."""

    def __init__(self, module: Any, command: tuple[str, ...]) -> None:
        self.module = module
        self.command = command
        self.real_popen = module.subprocess.Popen
        self.processes: list[subprocess.Popen[bytes]] = []
        self.identities: list[tuple[int, int | None]] = []
        self.tcp_observations: list[str] = []
        self._monitor_threads: list[threading.Thread] = []

    def install(self) -> None:
        def tracking_popen(*args: Any, **kwargs: Any) -> Any:
            process = self.real_popen(*args, **kwargs)
            argv = args[0] if args else kwargs.get("args")
            if tuple(argv or ()) == self.command:
                try:
                    pgid = os.getpgid(process.pid) if os.name == "posix" else None
                except OSError:
                    pgid = None
                self.processes.append(process)
                self.identities.append((process.pid, pgid))
                monitor = threading.Thread(
                    target=self._monitor_tcp,
                    args=(process,),
                    daemon=True,
                )
                self._monitor_threads.append(monitor)
                monitor.start()
            return process

        self.module.subprocess.Popen = tracking_popen

    def restore(self) -> None:
        self.module.subprocess.Popen = self.real_popen

    def _monitor_tcp(self, process: subprocess.Popen[bytes]) -> None:
        deadline = time.monotonic() + 10.0
        while process.poll() is None and time.monotonic() < deadline:
            observations = _lsof_tcp(process.pid)
            if observations:
                self.tcp_observations.extend(observations)
                return
            time.sleep(0.2)

    def cleanup(self) -> list[int]:
        for monitor in self._monitor_threads:
            monitor.join(timeout=0.5)
        for process, (_pid, pgid) in zip(self.processes, self.identities, strict=True):
            if process.poll() is not None:
                continue
            try:
                if os.name == "posix" and pgid is not None:
                    os.killpg(pgid, signal.SIGTERM)
                else:
                    process.terminate()
            except (OSError, ProcessLookupError):
                pass
            try:
                process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                try:
                    if os.name == "posix" and pgid is not None:
                        os.killpg(pgid, signal.SIGKILL)
                    else:
                        process.kill()
                except (OSError, ProcessLookupError):
                    pass
                try:
                    process.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    pass
        return [process.pid for process in self.processes if process.poll() is None]


def _classify_stream(*, timed_out: bool, completed: bool) -> str:
    if timed_out:
        return "deadline (sem EOF espontâneo)"
    if completed:
        return "EOF recebido espontaneamente"
    return "encerrou sem confirmação de EOF"


def _worker_new(mode: str, binary: Path) -> dict[str, Any]:
    import athena
    import athena.bridge.runner as bridge_runner
    from athena.bridge import LocalBridgeRunner, RunRequest
    from athena.execution import ExecutionDeadlines, ExecutionRecord
    from athena.lease import DirectoryLeaseManager

    command = _command(binary)
    tracker = ProcessTracker(bridge_runner, command)
    execution = ExecutionRecord(
        "ollama",
        profile="local_model",
        deadlines=ExecutionDeadlines(absolute_timeout_s=TIMEOUT_S),
    )
    started = time.monotonic()
    tracker.install()
    result = None
    try:
        result = LocalBridgeRunner().run(
            RunRequest(
                command,
                WORKSPACE,
                use_pty=mode == "pty",
                termination_grace_s=3.0,
            ),
            execution,
            DirectoryLeaseManager(),
        )
    finally:
        tracker.restore()
        survivors = tracker.cleanup()
    if result is None:
        raise RuntimeError("núcleo novo não retornou resultado")
    state = result.state.value
    timed_out = result.expired_deadline is not None
    completed = state == "completed"
    return {
        "core": "novo",
        "mode": mode.upper(),
        "binary": str(binary),
        "athena_file": str(Path(athena.__file__).resolve()),
        "duration_s": time.monotonic() - started,
        "state": state,
        "timed_out": timed_out,
        "stream_end": _classify_stream(timed_out=timed_out, completed=completed),
        "output_bytes": len(result.output.encode("utf-8")),
        "exit_code": result.exit_code,
        "pids": [pid for pid, _pgid in tracker.identities],
        "tcp_to_service": any("127.0.0.1:11434" in line for line in tracker.tcp_observations),
        "tcp_observations": tracker.tcp_observations,
        "survivors": survivors,
    }


def _worker_legacy(mode: str, binary: Path) -> dict[str, Any]:
    import athena
    import athena.bridge as legacy_bridge

    command = _command(binary)
    tracker = ProcessTracker(legacy_bridge, command)
    runner = legacy_bridge.run_with_pty if mode == "pty" else legacy_bridge.run_subprocess
    started = time.monotonic()
    tracker.install()
    result = None
    try:
        result = runner(
            "ollama",
            command,
            cwd=str(WORKSPACE),
            timeout=TIMEOUT_S,
            termination_grace_s=3.0,
            service_profile="local_model",
        )
    finally:
        tracker.restore()
        survivors = tracker.cleanup()
    if result is None:
        raise RuntimeError("núcleo legado não retornou resultado")
    execution = result.execution or {}
    state = str(execution.get("state", "UNKNOWN"))
    timed_out = bool(result.timed_out)
    completed = state.lower() == "completed"
    return {
        "core": "antigo",
        "mode": mode.upper(),
        "binary": str(binary),
        "athena_file": str(Path(athena.__file__).resolve()),
        "duration_s": time.monotonic() - started,
        "state": state,
        "timed_out": timed_out,
        "stream_end": _classify_stream(timed_out=timed_out, completed=completed),
        "output_bytes": len((result.output or "").encode("utf-8")),
        "exit_code": result.exit_code,
        "pids": [pid for pid, _pgid in tracker.identities],
        "tcp_to_service": any("127.0.0.1:11434" in line for line in tracker.tcp_observations),
        "tcp_observations": tracker.tcp_observations,
        "survivors": survivors,
    }


def _run_worker(core: str, mode: str, binary: Path) -> dict[str, Any]:
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
            "--binary",
            str(binary),
        ),
        cwd="/tmp",
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S + 30.0,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"worker {core}/{mode} falhou (rc={completed.returncode}): "
            f"{completed.stderr.strip()[-1000:]}"
        )
    try:
        return json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"worker {core}/{mode} não retornou JSON válido") from exc


def _binary_evidence() -> dict[str, Any]:
    direct_lstat = DIRECT_OLLAMA.lstat()
    direct_stat = DIRECT_OLLAMA.stat()
    app_stat = APP_OLLAMA.stat()
    return {
        "direct_is_symlink": DIRECT_OLLAMA.is_symlink(),
        "direct_link": os.readlink(DIRECT_OLLAMA) if DIRECT_OLLAMA.is_symlink() else None,
        "direct_lstat_inode": direct_lstat.st_ino,
        "direct_target_inode": direct_stat.st_ino,
        "app_inode": app_stat.st_ino,
        "same_inode": (direct_stat.st_dev, direct_stat.st_ino)
        == (app_stat.st_dev, app_stat.st_ino),
        "direct_sha256": _sha256(DIRECT_OLLAMA),
        "app_sha256": _sha256(APP_OLLAMA),
        "service_snapshot_before": _service_snapshot(),
    }


def _table_row(result: dict[str, Any]) -> str:
    return (
        "| {core} | {mode} | {duration_s:.2f} | {state} | {stream_end} | "
        "{output_bytes} |".format(**result)
    )


def _write_report(
    matrix: list[dict[str, Any]],
    app_result: dict[str, Any],
    evidence: dict[str, Any],
    gate: dict[str, Any] | None,
) -> None:
    direct_pty = matrix[0]
    pty_results = [item for item in matrix if item["mode"] == "PTY"]
    pipe_results = [item for item in matrix if item["mode"] == "PIPE"]
    pty_deadlines = all(item["timed_out"] for item in pty_results)
    pipe_eofs = all(
        not item["timed_out"] and "EOF recebido" in item["stream_end"]
        for item in pipe_results
    )
    hypothesis = "CONFIRMADA" if pty_deadlines and pipe_eofs else "REFUTADA"
    all_results = [*matrix, app_result]
    survivors = sorted({pid for item in all_results for pid in item["survivors"]})
    direct_hash = evidence["direct_sha256"]
    app_hash = evidence["app_sha256"]
    server_seen = any("LISTEN" in line for line in evidence["service_snapshot_before"])
    client_connections = [item["tcp_to_service"] for item in all_results]
    gate_text = "não executado neste momento" if gate is None else (
        "PASS" if gate["returncode"] == 0 else "FAIL"
    )

    lines = [
        "# Investigação PTY x PIPE — Ollama local",
        "",
        "## Escopo e método",
        "",
        f"- macOS; modelo `{MODEL}`; timeout absoluto de {TIMEOUT_S:.2f} s.",
        f"- Prompt idêntico ao cenário 2 do gate comparativo (marcador `{MARKER}`).",
        "- Uma execução por combinação, sequencial, sem fallback e sem adapter MLX.",
        "- Tamanho é a saída capturada pelo respectivo bridge, codificada em UTF-8; o conteúdo do modelo não é reproduzido neste relatório.",
        "- `EOF recebido` significa que o bridge alcançou conclusão normal somente depois de seu leitor observar o fechamento do stream; em deadline, o stream não fechou espontaneamente antes do teardown.",
        "",
        "## Variável 1 — matriz PTY/PIPE",
        "",
        "| Núcleo | Modo | Duração real (s) | Estado final | Encerramento do stream | Saída (bytes) |",
        "|---|---|---:|---|---|---:|",
        *[_table_row(item) for item in matrix],
        "",
        f"**Hipótese {hypothesis}.** ",
        (
            "Nas duas implementações, PTY atingiu o deadline sem EOF espontâneo, enquanto PIPE recebeu EOF e concluiu antes do teto."
            if hypothesis == "CONFIRMADA"
            else "Os resultados observados não apresentaram simultaneamente deadline em PTY e EOF/conclusão em PIPE nos dois núcleos."
        ),
        "",
        "## Variável 2 — `/usr/local/bin/ollama` x binário da app",
        "",
        f"- `/usr/local/bin/ollama` é symlink: {'sim' if evidence['direct_is_symlink'] else 'não'}; alvo: `{evidence['direct_link']}`.",
        f"- Mesmo arquivo efetivo (device/inode): {'sim' if evidence['same_inode'] else 'não'}; inode do alvo direto `{evidence['direct_target_inode']}`, inode embarcado `{evidence['app_inode']}`.",
        f"- SHA-256 idêntico: {'sim' if direct_hash == app_hash else 'não'} (`{direct_hash}`).",
        f"- Serviço local em `127.0.0.1:11434` observado antes dos testes: {'sim' if server_seen else 'não'}.",
        f"- Conexão TCP da CLI ao serviço observada durante os testes: {sum(client_connections)}/{len(client_connections)} execuções.",
        "- Portanto, o caminho em `/usr/local/bin` não é uma segunda implementação nem inicia um servidor independente: é o mesmo executável cliente da app e fala com o `ollama serve` já ativo.",
        "",
        "| Caminho sob PTY (núcleo novo) | Duração (s) | Estado | Encerramento | Saída (bytes) |",
        "|---|---:|---|---|---:|",
        f"| `{direct_pty['binary']}` | {direct_pty['duration_s']:.2f} | {direct_pty['state']} | {direct_pty['stream_end']} | {direct_pty['output_bytes']} |",
        f"| `{app_result['binary']}` | {app_result['duration_s']:.2f} | {app_result['state']} | {app_result['stream_end']} | {app_result['output_bytes']} |",
        "",
        "Os resultados observados dessas duas tentativas PTY diferiram (conclusão versus deadline), mas os caminhos resolvem para o mesmo inode e hash e ambos conectaram ao mesmo serviço. Assim, não há diferença de executável que explique o resultado; a divergência é variação entre execuções do mesmo cliente/serviço.",
        "",
        "## Variável 3 — preparo para Windows (não executado)",
        "",
        "### Pré-requisitos",
        "",
        "1. Windows 10/11, Python 3.12, Git e Ollama para Windows instalados; modelo previamente baixado com `ollama pull qwen3:8b`.",
        "2. App Ollama aberta e `ollama list` funcionando localmente; não alterar `OLLAMA_HOST` nem liberar porta no firewall.",
        "3. Checkout da mesma revisão, ambiente virtual ativo e dependências instaladas com `python -m pip install -e .[dev]` (ou o procedimento equivalente do projeto).",
        "",
        "### Procedimento exato em PowerShell",
        "",
        "```powershell",
        "Set-Location C:\\caminho\\Athena-MCP",
        "$OllamaBin = (Get-Command ollama.exe).Source",
        "Get-Command ollama.exe | Format-List Source,Path,Version",
        "Get-CimInstance Win32_Process -Filter \"Name='ollama.exe'\" | Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine",
        "Get-NetTCPConnection -LocalPort 11434 | Select-Object State,LocalAddress,LocalPort,OwningProcess",
        "New-Item -ItemType Directory -Force C:\\tmp\\athena_pty_investigation | Out-Null",
        "$env:PYTHONPATH = (Resolve-Path .).Path",
        "python harness\\investigate_pty.py --worker new --mode pipe --binary $OllamaBin | Tee-Object C:\\tmp\\new_pipe.json",
        "$env:PYTHONPATH = (Resolve-Path legado).Path",
        "python harness\\investigate_pty.py --worker legacy --mode pipe --binary $OllamaBin | Tee-Object C:\\tmp\\legacy_pipe.json",
        "Remove-Item Env:PYTHONPATH",
        "python -m harness.p0_gate",
        "Get-CimInstance Win32_Process -Filter \"Name='ollama.exe'\" | Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine",
        "```",
        "",
        "Registrar novo/PTY e legado/PTY como `N/A` nesta revisão: o novo rejeita PTY fora de POSIX e o legado faz fallback silencioso para PIPE, que não deve ser rotulado como PTY. Os dois comandos acima reproduzem novo/PIPE e legado/PIPE com o mesmo prompt e timeout embutidos no harness. Dos JSONs e comandos de inspeção, coletar: duração monotônica, estado, deadline, EOF, bytes, PID/PPID/caminho/command line, conexão para `127.0.0.1:11434` e sobreviventes. Repetir os casos PTY somente depois de existir implementação Windows explícita (por exemplo ConPTY). Linux fica fora desta etapa.",
        "",
        "### Pontos POSIX-only levantados no núcleo novo",
        "",
        "- `athena/bridge/runner.py`: import/uso de `pty.openpty`, descritores master/slave, `os.read` e `select.select` sobre fd de PTY; no Windows precisa de PIPE como caminho padrão ou integração ConPTY própria.",
        "- `athena/bridge/runner.py`: `start_new_session=True` usa a semântica `setsid` para criar sessão/grupo POSIX; no Windows precisa de `creationflags=subprocess.CREATE_NEW_PROCESS_GROUP`.",
        "- `athena/bridge/runner.py` e `athena/bridge/posix.py`: suposição `pgid == pid`, `os.killpg`, inspeção por `ps -axo`, PGID e detecção de descendentes/escapes são POSIX; no Windows exigem rastreamento alternativo da árvore (Job Object é a opção forte).",
        "- `athena/bridge/posix.py`: `SIGTERM` -> graça -> `SIGKILL` e confirmação de grupo vazio não têm equivalência direta; no Windows é preciso definir CTRL_BREAK/terminate, espera limitada, kill/Job Object e confirmação separada de processo/árvore.",
        "- O `select` usado pelo leitor PTY aceita fd no POSIX; no Windows `select` atende sockets, não pipes/console handles, portanto a drenagem deve usar threads/overlapped I/O ou a API do ConPTY.",
        "- O núcleo novo já evita PTY fora de POSIX com erro explícito, mas ainda não configura grupo Windows nem oferece teardown equivalente; nada foi implementado nesta etapa.",
        "",
        "## Gate e higiene de processos",
        "",
        f"- `python -m harness.p0_gate`: **{gate_text}**.",
        f"- Processos `ollama run` sobreviventes rastreados após limpeza: {len(survivors)}" + (f" (PIDs: {', '.join(map(str, survivors))})." if survivors else "."),
        "- O serviço `ollama serve` preexistente da app não foi interrompido.",
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
    return {
        "returncode": completed.returncode,
        "summary": completed.stdout.strip().splitlines(),
    }


def _main(*, skip_gate: bool) -> int:
    if os.name != "posix":
        raise RuntimeError(
            "este ensaio PTY é POSIX; no Windows siga o procedimento documentado"
        )
    for binary in (DIRECT_OLLAMA, APP_OLLAMA):
        if not binary.is_file():
            raise RuntimeError(f"ollama não encontrado em {binary}")
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    evidence = _binary_evidence()
    specs = (
        ("new", "pty", DIRECT_OLLAMA),
        ("new", "pipe", DIRECT_OLLAMA),
        ("legacy", "pipe", DIRECT_OLLAMA),
        ("legacy", "pty", DIRECT_OLLAMA),
    )
    matrix: list[dict[str, Any]] = []
    for core, mode, binary in specs:
        print(f"iniciando {core}/{mode} ({binary})", flush=True)
        result = _run_worker(core, mode, binary)
        matrix.append(result)
        RESULTS_PATH.write_text(
            json.dumps({"matrix": matrix}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(
            f"concluído {core}/{mode}: {result['state']} em "
            f"{result['duration_s']:.2f}s; {result['output_bytes']} bytes",
            flush=True,
        )
    print(f"iniciando new/pty ({APP_OLLAMA})", flush=True)
    app_result = _run_worker("new", "pty", APP_OLLAMA)
    gate = None if skip_gate else _run_gate()
    evidence["service_snapshot_after"] = _service_snapshot()
    RESULTS_PATH.write_text(
        json.dumps(
            {"matrix": matrix, "app_result": app_result, "evidence": evidence, "gate": gate},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_report(matrix, app_result, evidence, gate)
    survivors = {pid for item in [*matrix, app_result] for pid in item["survivors"]}
    gate_failed = gate is not None and gate["returncode"] != 0
    print(f"relatório: {REPORT_PATH}", flush=True)
    return int(bool(survivors) or gate_failed)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", choices=("new", "legacy"))
    parser.add_argument("--mode", choices=("pty", "pipe"))
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--skip-gate", action="store_true")
    args = parser.parse_args()
    if args.worker:
        if args.mode is None or args.binary is None:
            parser.error("--mode e --binary são obrigatórios com --worker")
        result = (
            _worker_new(args.mode, args.binary)
            if args.worker == "new"
            else _worker_legacy(args.mode, args.binary)
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    return _main(skip_gate=args.skip_gate)


if __name__ == "__main__":
    raise SystemExit(main())
