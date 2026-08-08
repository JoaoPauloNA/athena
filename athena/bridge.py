from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

HAS_PTY = sys.platform != "win32"
if HAS_PTY:
    import pty
    import select

ANSI_ESCAPE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"
    r"|\x1b\][^\x07]*(?:\x07|\x1b\\)"
    r"|\x1b[@-Z\\-_]"
    r"|\r"
)


def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE.sub("", text)


def _stream_to_str(value: object) -> str:
    """Normaliza stdout/stderr de subprocess (str|bytes|None) para str."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def clean_cli_output(text: str) -> str:
    lines = []
    for line in strip_ansi(_stream_to_str(text)).splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")):
            continue
        lines.append(stripped)
    return "\n".join(lines).strip()


def _enriched_env(extra: Optional[dict] = None) -> dict:
    """Prefixa diretórios comuns de CLIs no PATH (hosts GUI costumam ter PATH mínimo).

    Cross-platform:
    - Windows: ~/.local/bin, %LOCALAPPDATA%\\Programs, npm global (%APPDATA%\\npm),
      pip --user Scripts, Scoop shims, Chocolatey, WinGet Links, cargo.
    - macOS:   ~/.local/bin, Homebrew (Apple Silicon e Intel), ~/.npm-global, cargo, go, bun.
    - Linux:   ~/.local/bin, /usr/local/bin, snap, flatpak exports, ~/.npm-global, cargo, go.
    Só diretórios existentes entram (os.path.isdir), então é seguro listar todos.
    """
    merged = os.environ.copy()
    home = os.path.expanduser("~")
    if sys.platform == "win32":
        local_app = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
        roaming = os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
        extras = (
            os.path.join(home, ".local", "bin"),
            os.path.join(local_app, "Programs"),
            os.path.join(local_app, "Programs", "codex"),
            os.path.join(roaming, "npm"),
            os.path.join(local_app, "Microsoft", "WinGet", "Links"),
            os.path.join(home, "scoop", "shims"),
            os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "chocolatey", "bin"),
            os.path.join(home, ".cargo", "bin"),
            os.path.join(home, "go", "bin"),
        )
        # pip --user: %APPDATA%\Python\Python3X\Scripts
        py_dir = os.path.join(roaming, "Python")
        if os.path.isdir(py_dir):
            for entry in sorted(os.listdir(py_dir)):
                scripts = os.path.join(py_dir, entry, "Scripts")
                if os.path.isdir(scripts):
                    extras += (scripts,)
    elif sys.platform == "darwin":
        extras = (
            os.path.join(home, ".local", "bin"),
            "/opt/homebrew/bin",          # Homebrew Apple Silicon
            "/usr/local/bin",             # Homebrew Intel / instaladores clássicos
            os.path.join(home, ".npm-global", "bin"),
            os.path.join(home, ".cargo", "bin"),
            os.path.join(home, "go", "bin"),
            os.path.join(home, ".bun", "bin"),
        )
    else:  # Linux e demais POSIX
        extras = (
            os.path.join(home, ".local", "bin"),
            "/usr/local/bin",
            "/snap/bin",
            "/var/lib/flatpak/exports/bin",
            os.path.join(home, ".local", "share", "flatpak", "exports", "bin"),
            os.path.join(home, ".npm-global", "bin"),
            os.path.join(home, ".cargo", "bin"),
            os.path.join(home, "go", "bin"),
            os.path.join(home, ".bun", "bin"),
        )

    def _merge_path(base: str) -> str:
        parts = [p for p in base.split(os.pathsep) if p]
        for path in extras:
            if path and os.path.isdir(path) and path not in parts:
                parts.insert(0, path)
        return os.pathsep.join(parts)

    merged["PATH"] = _merge_path(merged.get("PATH", ""))
    if extra:
        caller_path = extra.get("PATH")
        patched = dict(extra)
        if caller_path is not None:
            patched["PATH"] = _merge_path(caller_path)
        merged.update(patched)
    return merged


@dataclass
class RunResult:
    provider: str
    command: List[str]
    output: str
    exit_code: int
    timed_out: bool = False
    error: Optional[str] = None
    role: Optional[str] = None
    report_format_ok: Optional[bool] = None
    duration_s: float = 0.0
    warnings: List[str] = field(default_factory=list)
    telemetry: Optional[dict] = None
    verdict: Optional[dict] = None  # veredito do verificador, se verify=true

    def to_dict(self) -> dict:
        payload = {
            "provider": self.provider,
            "command": self.command,
            "output": self.output,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "error": self.error,
        }
        if self.role is not None:
            payload["role"] = self.role
        if self.report_format_ok is not None:
            payload["report_format_ok"] = self.report_format_ok
        if self.warnings:
            payload["warnings"] = self.warnings
        if self.telemetry is not None:
            payload["telemetry"] = self.telemetry
        if self.verdict is not None:
            payload["verdict"] = self.verdict
        return payload


def which(binary: str) -> Optional[str]:
    """Localiza um binário usando o PATH enriquecido (inclui ~/.local/bin,
    Homebrew etc.), não apenas o PATH cru do processo — hosts GUI costumam
    ter PATH mínimo e esconder CLIs instaladas."""
    path = shutil.which(binary)
    if path:
        return path
    return shutil.which(binary, path=_enriched_env().get("PATH"))


def run_subprocess(
    provider: str,
    command: Sequence[str],
    *,
    cwd: Optional[str] = None,
    timeout: int = 300,
    env: Optional[dict] = None,
    input_text: Optional[str] = None,
) -> RunResult:
    cmd = list(command)
    if sys.platform == "win32" and cmd and str(cmd[0]).lower().endswith((".cmd", ".bat")):
        # shims .cmd/.bat (npm CLIs no Windows) exigem o cmd.exe para executar
        cmd = ["cmd", "/c"] + cmd
    merged_env = _enriched_env(env)
    start = time.monotonic()

    try:
        run_kwargs: dict = {
            "capture_output": True,
            "text": True,
            "timeout": timeout,
            "cwd": cwd,
            "env": merged_env,
        }
        if input_text is None:
            run_kwargs["stdin"] = subprocess.DEVNULL
        else:
            run_kwargs["input"] = input_text

        completed = subprocess.run(cmd, **run_kwargs)
        output = clean_cli_output(
            _stream_to_str(completed.stdout)
            + ("\n" + _stream_to_str(completed.stderr) if completed.stderr else "")
        )
        return RunResult(
            provider=provider,
            command=cmd,
            output=output,
            exit_code=completed.returncode,
            duration_s=time.monotonic() - start,
        )
    except subprocess.TimeoutExpired as exc:
        partial = clean_cli_output(
            _stream_to_str(exc.stdout)
            + ("\n" + _stream_to_str(exc.stderr) if exc.stderr else "")
        )
        return RunResult(
            provider=provider,
            command=cmd,
            output=partial,
            exit_code=124,
            timed_out=True,
            error=f"Timeout após {timeout}s",
            duration_s=time.monotonic() - start,
        )
    except FileNotFoundError:
        return RunResult(
            provider=provider,
            command=cmd,
            output="",
            exit_code=127,
            error=f"Comando não encontrado: {cmd[0]}",
            duration_s=time.monotonic() - start,
        )
    except OSError as exc:
        return RunResult(
            provider=provider,
            command=cmd,
            output="",
            exit_code=1,
            error=str(exc),
            duration_s=time.monotonic() - start,
        )


def run_with_pty(
    provider: str,
    command: Sequence[str],
    *,
    cwd: Optional[str] = None,
    timeout: int = 300,
    env: Optional[dict] = None,
) -> RunResult:
    """Executa CLI que exige TTY (ex.: agy -p) via pseudo-terminal."""
    if not HAS_PTY:
        return run_subprocess(provider, command, cwd=cwd, timeout=timeout, env=env)

    cmd = list(command)
    merged_env = _enriched_env(env)
    merged_env.setdefault("TERM", "dumb")

    master_fd: Optional[int] = None
    slave_fd: Optional[int] = None
    proc: Optional[subprocess.Popen] = None
    start = time.monotonic()

    try:
        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            cmd,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=cwd,
            close_fds=True,
            env=merged_env,
        )
        os.close(slave_fd)
        slave_fd = None

        chunks: List[bytes] = []
        deadline = time.monotonic() + timeout

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                proc.kill()
                proc.wait(timeout=5)
                raw = b"".join(chunks).decode("utf-8", errors="replace")
                return RunResult(
                    provider=provider,
                    command=cmd,
                    output=clean_cli_output(raw),
                    exit_code=124,
                    timed_out=True,
                    error=f"Timeout após {timeout}s",
                    duration_s=time.monotonic() - start,
                )

            ready, _, _ = select.select([master_fd], [], [], min(0.2, remaining))
            if ready:
                try:
                    data = os.read(master_fd, 65536)
                except OSError:
                    data = b""
                if not data:
                    break
                chunks.append(data)

            if proc.poll() is not None:
                while True:
                    ready, _, _ = select.select([master_fd], [], [], 0.05)
                    if not ready:
                        break
                    try:
                        data = os.read(master_fd, 65536)
                    except OSError:
                        break
                    if not data:
                        break
                    chunks.append(data)
                break

        remaining = max(0.1, deadline - time.monotonic())
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
            raw = b"".join(chunks).decode("utf-8", errors="replace")
            return RunResult(
                provider=provider,
                command=cmd,
                output=clean_cli_output(raw),
                exit_code=124,
                timed_out=True,
                error=f"Timeout após {timeout}s",
                duration_s=time.monotonic() - start,
            )

        while True:
            ready, _, _ = select.select([master_fd], [], [], 0.05)
            if not ready:
                break
            try:
                data = os.read(master_fd, 65536)
            except OSError:
                break
            if not data:
                break
            chunks.append(data)

        raw = b"".join(chunks).decode("utf-8", errors="replace")
        return RunResult(
            provider=provider,
            command=cmd,
            output=clean_cli_output(raw),
            exit_code=proc.returncode if proc.returncode is not None else 1,
            duration_s=time.monotonic() - start,
        )
    except FileNotFoundError:
        return RunResult(
            provider=provider,
            command=cmd,
            output="",
            exit_code=127,
            error=f"Comando não encontrado: {cmd[0]}",
            duration_s=time.monotonic() - start,
        )
    except OSError as exc:
        return RunResult(
            provider=provider,
            command=cmd,
            output="",
            exit_code=1,
            error=str(exc),
            duration_s=time.monotonic() - start,
        )
    finally:
        if slave_fd is not None:
            try:
                os.close(slave_fd)
            except OSError:
                pass
        if master_fd is not None:
            try:
                os.close(master_fd)
            except OSError:
                pass
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
