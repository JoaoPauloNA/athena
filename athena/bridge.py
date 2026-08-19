from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from athena.execution import ExecutionControl, ExecutionRecord, ExecutionState, normalize_cancel_reason
from athena.flight_recorder import FlightRecorderSession
from athena.workspace_observer import WorkspaceChangeObserver

HAS_PTY = sys.platform != "win32"
if HAS_PTY:
    import pty
    import select

# POSIX: a process launched with start_new_session=True becomes the leader of
# its own session/process group (pgid == pid), so signalling that pgid reaches
# the owned group it spawned *unless* a descendant escapes via its own
# os.setsid()/setpgid() call. A process-table snapshot can detect some current
# escapes positively, but cannot prove their absence across races/reparenting.
HAS_PROCESS_GROUPS = sys.platform != "win32"

# Bounded grace window between SIGTERM and SIGKILL when tearing down a
# process group that has not exited on its own after a timeout/cancel.
TERMINATION_GRACE_S = 3.0

# How often the deadline-watch loop polls process liveness / deadline status.
# Bounds worst-case detection latency past a deadline to this interval; kept
# short enough that timing-boundary tests (near-exact-deadline assertions)
# stay tight.
_DEADLINE_POLL_INTERVAL_S = 0.05
_CANCEL_DRAIN_JOIN_BUDGET_S = 0.25
# After the direct CLI wrapper exits with no captured output and closed pipes,
# allow a short window for a same-pgid worker to start before stray cleanup.
_POST_DIRECT_EXIT_OUTPUT_GRACE_S = 0.5


def _with_flight_log(
    flight: FlightRecorderSession,
    record: ExecutionRecord,
    result: RunResult,
    *,
    stdout: str | None = None,
    stderr: str | None = None,
) -> RunResult:
    flight.finalize_terminal(result, record, stdout=stdout, stderr=stderr)
    return result


def _join_readers(stdout_thread: threading.Thread, stderr_thread: threading.Thread, budget_s: float = 5.0) -> bool:
    """Join both reader threads within a single shared time budget.

    A reader thread can block forever on stream.read() if its pipe fd stays
    open in a surviving grandchild (see the orphaned-grandchild test) --
    joining each with its own separate budget would double the worst-case
    wait. Threads are daemons, so giving up here just stops waiting; it does
    not leak anything the process wouldn't already leak.

    Returns True only when both reader threads finished (stdout/stderr fds
    observed closed from the parent side).
    """
    deadline = time.monotonic() + budget_s
    stdout_thread.join(timeout=max(0.0, deadline - time.monotonic()))
    stderr_thread.join(timeout=max(0.0, deadline - time.monotonic()))
    return not stdout_thread.is_alive() and not stderr_thread.is_alive()


def _reader_thread(stream, chunks: list[str], on_progress) -> None:
    """Drain `stream` incrementally into `chunks`, calling `on_progress()` per chunk.

    Runs on a background thread so the caller's deadline-watch loop can keep
    polling process liveness/deadlines without blocking on I/O. Each
    non-empty read is exactly the deterministic, observable progress signal
    this module tracks -- no semantic interpretation of the bytes happens
    here or anywhere in this module.

    Uses binary ``read1()`` so already-buffered pipe bytes are returned
    immediately instead of blocking until a full TextIOWrapper buffer or EOF.
    """
    try:
        while True:
            if hasattr(stream, "read1"):
                data = stream.read1(4096)
            else:
                data = stream.read(4096)
            if not data:
                break
            if isinstance(data, bytes):
                chunks.append(data.decode("utf-8", errors="replace"))
            else:
                chunks.append(data)
            on_progress()
    except (ValueError, OSError):
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _poll_until(predicate, deadline: float, interval: float = 0.05) -> bool:
    """Poll `predicate()` until it is truthy or `deadline` (monotonic) passes."""
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists under different privileges; treat as alive/unknown.
        return True
    return True


def _alive_escaped_descendants(root_pid: int, owned_pgid: int) -> set[int]:
    return {pid for pid in _escaped_descendants(root_pid, owned_pgid) if _pid_alive(pid)}


def _all_descendants(root_pid: int) -> set[int]:
    """Best-effort discovery of all descendants of `root_pid` in the process table."""
    if not HAS_PROCESS_GROUPS:
        return set()
    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid=,ppid="],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if completed.returncode != 0:
        return set()
    rows: list[tuple[int, int]] = []
    for line in completed.stdout.splitlines():
        pieces = line.split()
        if len(pieces) != 2:
            continue
        try:
            rows.append((int(pieces[0]), int(pieces[1])))
        except ValueError:
            continue
    descendants = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, parent_pid in rows:
            if parent_pid in descendants and pid not in descendants:
                descendants.add(pid)
                changed = True
    descendants.discard(root_pid)
    return descendants


def _alive_descendants(root_pid: int) -> set[int]:
    return {pid for pid in _all_descendants(root_pid) if _pid_alive(pid)}


def _resolve_post_direct_exit(
    *,
    proc: subprocess.Popen,
    pgid: int | None,
    record: ExecutionRecord,
    streams_open: Callable[[], bool],
    captured_output: Callable[[], bool],
) -> bool:
    """Block until owned-group workers finish or classify escaped survivors.

    Returns True when escaped descendants remain alive outside the owned pgid
    (TERMINATION_UNCONFIRMED). Otherwise returns False so the caller can
    finalize normally or run stray cleanup when the group is still non-empty.

    While stdout/stderr pipes remain open, or captured output already arrived,
    keep waiting for the owned pgid to drain naturally (Cursor-style workers).
    When pipes are closed and no output was captured, allow a short grace
    window for a silent same-pgid worker to start before stray cleanup.
    """
    if pgid is None or not HAS_PROCESS_GROUPS:
        return False
    if _alive_escaped_descendants(proc.pid, pgid):
        return True
    if _process_group_is_empty(pgid):
        return False

    direct_exit_at = time.monotonic()
    while record.deadline_status() is None and not _process_group_is_empty(pgid):
        if _alive_escaped_descendants(proc.pid, pgid):
            return True
        if streams_open() or captured_output():
            time.sleep(_DEADLINE_POLL_INTERVAL_S)
            continue
        if time.monotonic() - direct_exit_at >= _POST_DIRECT_EXIT_OUTPUT_GRACE_S:
            break
        time.sleep(_DEADLINE_POLL_INTERVAL_S)
    return False


def _unconfirmed_escaped_descendants_result(
    *,
    record: ExecutionRecord,
    provider: str,
    cmd: list[str],
    start: float,
    stdout: str,
    stderr: str,
    output: str,
    original_returncode: int | None = None,
) -> RunResult:
    record.confirm_direct_process_terminated()
    reason = (
        "processo direto encerrou, mas descendentes fora do pgid possuído "
        "continuam ativos; término/output real não confirmados"
    )
    if record.state == ExecutionState.RUNNING:
        record.transition(ExecutionState.TERMINATING, reason=reason)
    record.transition(ExecutionState.TERMINATION_UNCONFIRMED, reason=reason)
    exit_code = 125 if original_returncode in (None, 0) else original_returncode
    return RunResult(
        provider=provider,
        command=cmd,
        output=output,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        error=(
            "Terminação indeterminada: trabalho continua fora do escopo "
            "controlado pelo Athena"
        ),
        duration_s=time.monotonic() - start,
        telemetry={"original_returncode": original_returncode},
        execution=record.to_dict(),
    )


def _unconfirmed_streams_open_result(
    *,
    record: ExecutionRecord,
    provider: str,
    cmd: list[str],
    start: float,
    stdout: str,
    stderr: str,
    output: str,
    original_returncode: int | None = None,
) -> RunResult:
    record.confirm_direct_process_terminated()
    reason = (
        "processo direto encerrou, mas descritores stdout/stderr permanecem "
        "abertos; término/output real não confirmados"
    )
    if record.state == ExecutionState.RUNNING:
        record.transition(ExecutionState.TERMINATING, reason=reason)
    record.transition(ExecutionState.TERMINATION_UNCONFIRMED, reason=reason)
    exit_code = 125 if original_returncode in (None, 0) else original_returncode
    return RunResult(
        provider=provider,
        command=cmd,
        output=output,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        error=(
            "Terminação indeterminada: stdout/stderr ainda abertos após "
            "saída do processo direto"
        ),
        duration_s=time.monotonic() - start,
        telemetry={"original_returncode": original_returncode},
        execution=record.to_dict(),
    )


def _unconfirmed_empty_capture_result(
    *,
    record: ExecutionRecord,
    provider: str,
    cmd: list[str],
    start: float,
    stdout: str,
    stderr: str,
    output: str,
    original_returncode: int | None = None,
    reason: str | None = None,
) -> RunResult:
    record.confirm_direct_process_terminated()
    resolved_reason = reason or (
        "processo direto encerrou com exit 0, mas nenhum output foi "
        "capturado; término/output real não confirmados"
    )
    if record.state == ExecutionState.RUNNING:
        record.transition(ExecutionState.TERMINATING, reason=resolved_reason)
    record.transition(ExecutionState.TERMINATION_UNCONFIRMED, reason=resolved_reason)
    exit_code = 125 if original_returncode in (None, 0) else original_returncode
    return RunResult(
        provider=provider,
        command=cmd,
        output=output,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        error=(
            "Terminação indeterminada: CLI encerrou sem output capturável"
        ),
        duration_s=time.monotonic() - start,
        telemetry={"original_returncode": original_returncode},
        execution=record.to_dict(),
    )


def _process_group_is_empty(pgid: int) -> bool:
    """Best-effort check that no process remains in `pgid`.

    Signal 0 sends nothing but still raises ProcessLookupError when the
    target (here, an entire process group) no longer exists.
    """
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        # A member survived under different privileges; cannot confirm empty.
        return False
    return False


def _escaped_descendants(root_pid: int, owned_pgid: int) -> set[int]:
    """Best-effort discovery of descendants that left the owned POSIX group.

    This is a conservative signal, not a proof that no escape occurred: a
    descendant can exit/reparent between observations.  A positive result is
    nevertheless enough to forbid confirming even Athena's owned scope.
    """

    if not HAS_PROCESS_GROUPS:
        return set()
    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,pgid="],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if completed.returncode != 0:
        return set()
    rows: list[tuple[int, int, int]] = []
    for line in completed.stdout.splitlines():
        pieces = line.split()
        if len(pieces) != 3:
            continue
        try:
            rows.append((int(pieces[0]), int(pieces[1]), int(pieces[2])))
        except ValueError:
            continue
    descendants = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, parent_pid, _ in rows:
            if parent_pid in descendants and pid not in descendants:
                descendants.add(pid)
                changed = True
    return {
        pid
        for pid, _parent_pid, process_group in rows
        if pid != root_pid and pid in descendants and process_group != owned_pgid
    }


def terminate_process_tree(
    proc: subprocess.Popen,
    pgid: int | None,
    *,
    grace_s: float = TERMINATION_GRACE_S,
) -> tuple[bool, bool]:
    """Escalated, idempotent teardown of `proc` and (if possible) its group.

    Sends SIGTERM first, waits up to `grace_s` for a graceful exit, then
    escalates to SIGKILL. Safe to call more than once or after the process
    is already gone — every signal send is guarded, so a repeat call (e.g.
    a second cancellation request racing the first) is a no-op rather than
    an error.

    Returns (direct_process_terminated_confirmed, owned_scope_terminated_confirmed).
    The second value is carried in the legacy 0.x metadata field
    ``process_tree_terminated_confirmed`` but only proves Athena's owned
    process group plus the best-effort escape observation described above.
    On Windows, only the direct process is controlled/confirmed.
    """
    use_pgroup = HAS_PROCESS_GROUPS and pgid is not None
    escaped_descendants = (
        _escaped_descendants(proc.pid, pgid) if use_pgroup and proc.poll() is None else set()
    )

    if use_pgroup:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    else:
        try:
            proc.terminate()
        except OSError:
            pass

    deadline = time.monotonic() + grace_s
    _poll_until(lambda: proc.poll() is not None, deadline)

    if use_pgroup:
        # The direct leader may have exited on its own SIGTERM handling
        # while another member of the *same* pgid (e.g. a plain child the
        # leader spawned without its own setsid()) ignored the signal and
        # is still running -- inspect actual group emptiness rather than
        # trusting the leader's exit status alone.
        if not _process_group_is_empty(pgid):
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            _poll_until(lambda: _process_group_is_empty(pgid), time.monotonic() + 5)
    elif proc.poll() is None:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    direct_confirmed = proc.poll() is not None

    tree_confirmed = False
    if use_pgroup and direct_confirmed and not escaped_descendants:
        tree_confirmed = _process_group_is_empty(pgid)

    return direct_confirmed, tree_confirmed


def _cancel_result(
    *,
    record: ExecutionRecord,
    proc: subprocess.Popen | None,
    pgid: int | None,
    reason: str,
    provider: str,
    cmd: list[str],
    start: float,
    stdout: str,
    stderr: str,
    output: str,
    grace_s: float,
    remote_execution: bool = False,
) -> RunResult:
    if record.state not in (ExecutionState.CANCELLATION_REQUESTED, ExecutionState.TERMINATING):
        record.transition(ExecutionState.CANCELLATION_REQUESTED, reason=reason)
    if record.state != ExecutionState.TERMINATING:
        record.transition(ExecutionState.TERMINATING, reason=reason)

    direct_confirmed = False
    tree_confirmed = False
    if proc is not None:
        direct_confirmed, tree_confirmed = terminate_process_tree(proc, pgid, grace_s=grace_s)
        if direct_confirmed:
            record.confirm_direct_process_terminated()
        if tree_confirmed:
            record.confirm_process_tree_terminated()

    remote_unconfirmed = (
        remote_execution
        and record.remote_session_started
        and record.remote_termination_confirmed is not True
    )
    final_state = (
        ExecutionState.TERMINATION_UNCONFIRMED
        if remote_unconfirmed
        else (ExecutionState.CANCELLED if tree_confirmed else ExecutionState.TERMINATION_UNCONFIRMED)
    )
    record.transition(final_state, reason=reason)
    return RunResult(
        provider=provider,
        command=cmd,
        output=output,
        exit_code=130,
        stdout=stdout,
        stderr=stderr,
        timed_out=False,
        error=reason,
        duration_s=time.monotonic() - start,
        execution=record.to_dict(),
    )


def _terminate_after_runtime_exception(
    *,
    record: ExecutionRecord,
    proc: subprocess.Popen | None,
    pgid: int | None,
    reason: str,
    grace_s: float,
    remote_execution: bool,
) -> ExecutionState:
    """Apply termination pipeline for post-launch runtime exceptions."""
    if proc is None or not record.process_created:
        record.transition(ExecutionState.FAILED, reason=reason)
        return ExecutionState.FAILED

    if record.state not in (ExecutionState.CANCELLATION_REQUESTED, ExecutionState.TERMINATING):
        record.transition(ExecutionState.TERMINATING, reason=reason)
    direct_confirmed, tree_confirmed = terminate_process_tree(proc, pgid, grace_s=grace_s)
    if direct_confirmed:
        record.confirm_direct_process_terminated()
    if tree_confirmed:
        record.confirm_process_tree_terminated()

    remote_unconfirmed = (
        remote_execution
        and record.remote_session_started
        and record.remote_termination_confirmed is not True
    )
    final_state = ExecutionState.TERMINATION_UNCONFIRMED if remote_unconfirmed else ExecutionState.FAILED
    record.transition(final_state, reason=reason)
    return final_state


def _finalize_after_natural_exit(
    *,
    record: ExecutionRecord,
    proc: subprocess.Popen,
    pgid: int | None,
    grace_s: float,
    provider: str,
    cmd: list[str],
    start: float,
    stdout: str,
    stderr: str,
    output: str,
    require_captured_output: bool = False,
    alive_tracked_descendants: set[int] | None = None,
) -> RunResult:
    """Finalize a naturally-exited direct process with tree confirmation.

    If the owned process group still has members after the direct process exits,
    run the same SIGTERM->grace->SIGKILL cleanup pipeline and only return a
    normal COMPLETED/FAILED outcome after positive tree-empty confirmation.
    """
    original_returncode = proc.returncode if proc.returncode is not None else 1
    record.confirm_direct_process_terminated()

    if alive_tracked_descendants:
        alive = {pid for pid in alive_tracked_descendants if _pid_alive(pid)}
        if alive:
            return _unconfirmed_escaped_descendants_result(
                record=record,
                provider=provider,
                cmd=cmd,
                start=start,
                stdout=stdout,
                stderr=stderr,
                output=output,
                original_returncode=original_returncode,
            )

    if pgid is not None and HAS_PROCESS_GROUPS:
        alive_escaped = _alive_escaped_descendants(proc.pid, pgid)
        if alive_escaped:
            return _unconfirmed_escaped_descendants_result(
                record=record,
                provider=provider,
                cmd=cmd,
                start=start,
                stdout=stdout,
                stderr=stderr,
                output=output,
                original_returncode=original_returncode,
            )

    if pgid is not None and not _process_group_is_empty(pgid):
        record.transition(
            ExecutionState.TERMINATING,
            reason="processo direto saiu naturalmente; limpando descendentes no mesmo pgid",
        )
        _, tree_confirmed = terminate_process_tree(proc, pgid, grace_s=grace_s)
        if tree_confirmed:
            record.confirm_process_tree_terminated()
        else:
            record.transition(
                ExecutionState.TERMINATION_UNCONFIRMED,
                reason=(
                    "processo direto saiu naturalmente, mas não foi possível confirmar "
                    "árvore vazia no pgid possuído"
                ),
            )
            return RunResult(
                provider=provider,
                command=cmd,
                output=output,
                exit_code=125 if original_returncode == 0 else original_returncode,
                stdout=stdout,
                stderr=stderr,
                error=(
                    "Terminação indeterminada: não foi possível confirmar limpeza "
                    "dos descendentes no pgid possuído"
                ),
                duration_s=time.monotonic() - start,
                telemetry={"original_returncode": original_returncode},
                execution=record.to_dict(),
            )
    elif pgid is not None:
        record.confirm_process_tree_terminated()

    if (
        require_captured_output
        and original_returncode == 0
        and not (output or "").strip()
    ):
        return _unconfirmed_empty_capture_result(
            record=record,
            provider=provider,
            cmd=cmd,
            start=start,
            stdout=stdout,
            stderr=stderr,
            output=output,
            original_returncode=original_returncode,
        )

    record.transition(
        ExecutionState.COMPLETED if original_returncode == 0 else ExecutionState.FAILED
    )
    return RunResult(
        provider=provider,
        command=cmd,
        output=output,
        exit_code=original_returncode,
        stdout=stdout,
        stderr=stderr,
        duration_s=time.monotonic() - start,
        execution=record.to_dict(),
    )

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


def _enriched_env(extra: dict | None = None) -> dict:
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
    command: list[str]
    output: str
    exit_code: int
    stdout: str | None = None
    stderr: str | None = None
    timed_out: bool = False
    error: str | None = None
    role: str | None = None
    report_format_ok: bool | None = None
    duration_s: float = 0.0
    warnings: list[str] = field(default_factory=list)
    telemetry: dict | None = None
    verdict: dict | None = None  # veredito do verificador, se verify=true
    execution: dict | None = None  # metadados do contrato de execução (athena.execution), se coletado
    # decisão da política de verificação reforçada (athena.reinforced_verification).
    # Sinal aditivo: nunca altera output/exit_code/verdict deste resultado.
    reinforced_verification: dict | None = None

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
        if self.stdout is not None:
            payload["stdout"] = self.stdout
        if self.stderr is not None:
            payload["stderr"] = self.stderr
        if self.report_format_ok is not None:
            payload["report_format_ok"] = self.report_format_ok
        if self.warnings:
            payload["warnings"] = self.warnings
        if self.telemetry is not None:
            payload["telemetry"] = self.telemetry
        if self.verdict is not None:
            payload["verdict"] = self.verdict
        if self.execution is not None:
            payload["execution"] = self.execution
        if self.reinforced_verification is not None:
            payload["reinforced_verification"] = self.reinforced_verification
        return payload


def which(binary: str) -> str | None:
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
    cwd: str | None = None,
    timeout: int = 300,
    idle_timeout: float | None = None,
    termination_grace_s: float | None = None,
    env: dict | None = None,
    input_text: str | None = None,
    execution_id: str | None = None,
    attempt_id: str | None = None,
    on_execution_update: Callable[[dict], None] | None = None,
    execution_control: ExecutionControl | None = None,
    remote_execution: bool = False,
    service_profile: str | None = None,
    require_captured_output: bool = False,
) -> RunResult:
    """Run `command` to completion, enforcing two independent deadlines.

    `timeout` is the absolute ceiling on total run time (as before). The new
    `idle_timeout` is optional and, when set, bounds how long the process may
    go without producing observable stdout/stderr output; each incremental
    chunk read resets it via ExecutionRecord.record_progress(). The absolute
    ceiling is always enforced even while idle_timeout keeps getting reset by
    a heartbeat -- see ExecutionRecord.deadline_status().
    """
    cmd = list(command)
    if sys.platform == "win32" and cmd and str(cmd[0]).lower().endswith((".cmd", ".bat")):
        # shims .cmd/.bat (npm CLIs no Windows) exigem o cmd.exe para executar
        cmd = ["cmd", "/c"] + cmd
    merged_env = _enriched_env(env)
    if cwd and sys.platform != "win32":
        # PWD é herdado do processo pai e NÃO é atualizado por subprocess.run(cwd=...).
        # CLIs que confiam em $PWD (confirmado: opencode) "escapam" do diretório
        # de trabalho e escrevem no projeto errado. Sincronizar PWD com cwd.
        merged_env["PWD"] = str(cwd)
    start = time.monotonic()
    record_kwargs: dict[str, str] = {}
    if execution_id is not None:
        record_kwargs["execution_id"] = execution_id
    if attempt_id is not None:
        record_kwargs["attempt_id"] = attempt_id
    record = ExecutionRecord(
        provider=provider,
        profile=service_profile,
        transport="ssh" if remote_execution else "local",
        **record_kwargs,
    )
    flight = FlightRecorderSession(
        execution_id=record.execution_id,
        attempt_id=record.attempt_id,
        provider=provider,
        profile=service_profile,
        transport=record.transport,
        command=cmd,
        cwd=cwd,
        env=merged_env,
    )
    record.on_update = flight.wrap_on_update(on_execution_update)
    record.transition(ExecutionState.STARTING)
    grace_s = TERMINATION_GRACE_S if termination_grace_s is None else termination_grace_s
    if execution_control is not None and execution_control.cancellation_requested:
        reason = normalize_cancel_reason(execution_control.cancel_reason)
        if execution_control.client_abandoned:
            record.mark_client_abandoned()
            reason = "client_abandoned"
        record.transition(ExecutionState.CANCELLED, reason=reason)
        return _with_flight_log(
            flight,
            record,
            RunResult(
                provider=provider,
                command=cmd,
                output="",
                exit_code=130,
                timed_out=False,
                duration_s=time.monotonic() - start,
                execution=record.to_dict(),
                error=reason,
            ),
        )

    popen_kwargs: dict = {
        "cwd": cwd,
        "env": merged_env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    popen_kwargs["stdin"] = subprocess.DEVNULL if input_text is None else subprocess.PIPE
    if HAS_PROCESS_GROUPS:
        # New session -> this process becomes its own group leader (pgid ==
        # pid), giving us an owned pgid to signal as a unit later.
        popen_kwargs["start_new_session"] = True
    elif sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    try:
        proc = subprocess.Popen(cmd, **popen_kwargs)
    except FileNotFoundError:
        # No process was ever created -- record.process_created stays False,
        # so no termination confirmation is required (see
        # router._fallback_block_reason).
        record.transition(ExecutionState.FAILED, reason="Comando não encontrado")
        return _with_flight_log(
            flight,
            record,
            RunResult(
                provider=provider,
                command=cmd,
                output="",
                exit_code=127,
                error=f"Comando não encontrado: {cmd[0]}",
                duration_s=time.monotonic() - start,
                execution=record.to_dict(),
            ),
        )
    except OSError as exc:
        # Same as above: Popen() failed before any process existed.
        record.transition(ExecutionState.FAILED, reason=str(exc))
        return _with_flight_log(
            flight,
            record,
            RunResult(
                provider=provider,
                command=cmd,
                output="",
                exit_code=1,
                error=str(exc),
                duration_s=time.monotonic() - start,
                execution=record.to_dict(),
            ),
        )

    pgid: int | None = None
    if HAS_PROCESS_GROUPS:
        try:
            pgid = os.getpgid(proc.pid)
        except OSError:
            pgid = None
    record.set_process_identity(proc.pid, pgid)
    if remote_execution:
        record.mark_remote_session_started()
    record.transition(ExecutionState.RUNNING)
    record.configure_deadlines(
        absolute_deadline_s=float(timeout),
        idle_deadline_s=float(idle_timeout) if idle_timeout is not None else None,
        termination_grace_s=grace_s,
    )

    observer = WorkspaceChangeObserver(
        workspace=cwd,
        record=record,
        flight=flight,
        poll_interval_s=0.5,
    )
    observer.start()

    try:
        if input_text is not None:
            def _write_stdin() -> None:
                try:
                    proc.stdin.write(input_text.encode("utf-8"))
                    proc.stdin.close()
                except (BrokenPipeError, OSError, ValueError):
                    pass

            threading.Thread(target=_write_stdin, daemon=True).start()

        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        stdout_thread = threading.Thread(
            target=_reader_thread, args=(proc.stdout, stdout_chunks, record.record_progress),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=_reader_thread, args=(proc.stderr, stderr_chunks, record.record_progress),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        deadline_reason: str | None = None
        cancel_reason = "user_requested"
        escaped_unconfirmed = False
        tracked_descendant_pids: set[int] = set()
        while True:
            if execution_control is not None and execution_control.cancellation_requested:
                cancel_reason = normalize_cancel_reason(execution_control.cancel_reason)
                if execution_control.client_abandoned:
                    record.mark_client_abandoned()
                    cancel_reason = "client_abandoned"
                break
            if pgid is not None and HAS_PROCESS_GROUPS and proc.poll() is None:
                tracked_descendant_pids.update(_alive_descendants(proc.pid))
                tracked_descendant_pids.update(_alive_escaped_descendants(proc.pid, pgid))
            # Deadline check comes first: a process observed as finished
            # (proc.poll() is not None) *after* its absolute/idle deadline
            # already expired must still be reported as timed out, not as a
            # success. Checking poll() first could let a late-but-real
            # completion race past an already-expired deadline undetected.
            deadline_reason = record.deadline_status()
            if deadline_reason is not None:
                break
            if proc.poll() is not None:
                tracked_descendant_pids.update(_alive_descendants(proc.pid))
                if pgid is not None and HAS_PROCESS_GROUPS:
                    tracked_descendant_pids.update(_alive_escaped_descendants(proc.pid, pgid))
                if _resolve_post_direct_exit(
                    proc=proc,
                    pgid=pgid,
                    record=record,
                    streams_open=lambda: stdout_thread.is_alive() or stderr_thread.is_alive(),
                    captured_output=lambda: bool(stdout_chunks or stderr_chunks),
                ):
                    escaped_unconfirmed = True
                elif any(_pid_alive(pid) for pid in tracked_descendant_pids):
                    escaped_unconfirmed = True
                deadline_reason = record.deadline_status()
                record.record_progress()  # subprocess completion is itself progress
                break
            time.sleep(_DEADLINE_POLL_INTERVAL_S)

        if execution_control is not None and execution_control.cancellation_requested:
            if record.state not in (ExecutionState.CANCELLATION_REQUESTED, ExecutionState.TERMINATING):
                record.transition(ExecutionState.CANCELLATION_REQUESTED, reason=cancel_reason)
            if record.state != ExecutionState.TERMINATING:
                record.transition(ExecutionState.TERMINATING, reason=cancel_reason)

            direct_confirmed, tree_confirmed = terminate_process_tree(proc, pgid, grace_s=grace_s)
            if direct_confirmed:
                record.confirm_direct_process_terminated()
            if tree_confirmed:
                record.confirm_process_tree_terminated()

            # Drain a short amount after termination attempt; don't delay cancellation
            # behind potentially blocked reader joins.
            _join_readers(
                stdout_thread,
                stderr_thread,
                budget_s=min(_CANCEL_DRAIN_JOIN_BUDGET_S, max(0.0, grace_s)),
            )
            stdout = clean_cli_output("".join(stdout_chunks))
            stderr = clean_cli_output("".join(stderr_chunks))
            output = "\n".join(part for part in (stdout, stderr) if part)
            remote_unconfirmed = (
                remote_execution
                and record.remote_session_started
                and record.remote_termination_confirmed is not True
            )
            final_state = (
                ExecutionState.TERMINATION_UNCONFIRMED
                if remote_unconfirmed
                else (
                    ExecutionState.CANCELLED
                    if tree_confirmed
                    else ExecutionState.TERMINATION_UNCONFIRMED
                )
            )
            record.transition(final_state, reason=cancel_reason)
            return _with_flight_log(
                flight,
                record,
                RunResult(
                    provider=provider,
                    command=cmd,
                    output=output,
                    exit_code=130,
                    stdout=stdout,
                    stderr=stderr,
                    timed_out=False,
                    error=cancel_reason,
                    duration_s=time.monotonic() - start,
                    execution=record.to_dict(),
                ),
                stdout=stdout,
                stderr=stderr,
            )

        if deadline_reason is None:
            streams_closed = _join_readers(stdout_thread, stderr_thread)
            stdout = clean_cli_output("".join(stdout_chunks))
            stderr = clean_cli_output("".join(stderr_chunks))
            output = "\n".join(part for part in (stdout, stderr) if part)
            if escaped_unconfirmed:
                return _with_flight_log(
                    flight,
                    record,
                    _unconfirmed_escaped_descendants_result(
                        record=record,
                        provider=provider,
                        cmd=cmd,
                        start=start,
                        stdout=stdout,
                        stderr=stderr,
                        output=output,
                        original_returncode=proc.returncode,
                    ),
                    stdout=stdout,
                    stderr=stderr,
                )
            if not streams_closed:
                return _with_flight_log(
                    flight,
                    record,
                    _unconfirmed_streams_open_result(
                        record=record,
                        provider=provider,
                        cmd=cmd,
                        start=start,
                        stdout=stdout,
                        stderr=stderr,
                        output=output,
                        original_returncode=proc.returncode,
                    ),
                    stdout=stdout,
                    stderr=stderr,
                )
            alive_tracked = {
                pid for pid in tracked_descendant_pids if _pid_alive(pid)
            }
            if require_captured_output and not (output or "").strip():
                return _with_flight_log(
                    flight,
                    record,
                    _unconfirmed_empty_capture_result(
                        record=record,
                        provider=provider,
                        cmd=cmd,
                        start=start,
                        stdout=stdout,
                        stderr=stderr,
                        output=output,
                        original_returncode=proc.returncode,
                    ),
                    stdout=stdout,
                    stderr=stderr,
                )
            return _with_flight_log(
                flight,
                record,
                _finalize_after_natural_exit(
                    record=record,
                    proc=proc,
                    pgid=pgid,
                    grace_s=grace_s,
                    provider=provider,
                    cmd=cmd,
                    start=start,
                    stdout=stdout,
                    stderr=stderr,
                    output=output,
                    require_captured_output=require_captured_output,
                    alive_tracked_descendants=alive_tracked,
                ),
                stdout=stdout,
                stderr=stderr,
            )

        if deadline_reason == "absolute_deadline":
            timeout_reason = f"Timeout após {timeout}s"
        else:
            timeout_reason = f"Timeout de inatividade após {idle_timeout}s sem progresso observável"
        record.transition(ExecutionState.CANCELLATION_REQUESTED, reason=timeout_reason)
        record.transition(ExecutionState.TERMINATING, reason=timeout_reason)

        direct_confirmed, tree_confirmed = terminate_process_tree(proc, pgid, grace_s=grace_s)
        if direct_confirmed:
            record.confirm_direct_process_terminated()
        if tree_confirmed:
            record.confirm_process_tree_terminated()

        _join_readers(stdout_thread, stderr_thread)
        stdout = clean_cli_output("".join(stdout_chunks))
        stderr = clean_cli_output("".join(stderr_chunks))
        partial = "\n".join(part for part in (stdout, stderr) if part)

        remote_unconfirmed = (
            remote_execution
            and record.remote_session_started
            and record.remote_termination_confirmed is not True
        )
        final_state = (
            ExecutionState.TERMINATION_UNCONFIRMED
            if remote_unconfirmed
            else (ExecutionState.CANCELLED if tree_confirmed else ExecutionState.TERMINATION_UNCONFIRMED)
        )
        record.transition(final_state, reason=timeout_reason)
        return _with_flight_log(
            flight,
            record,
            RunResult(
                provider=provider,
                command=cmd,
                output=partial,
                exit_code=124,
                stdout=stdout,
                stderr=stderr,
                timed_out=True,
                error=timeout_reason,
                duration_s=time.monotonic() - start,
                execution=record.to_dict(),
            ),
            stdout=stdout,
            stderr=stderr,
        )
    except OSError as exc:
        _terminate_after_runtime_exception(
            record=record,
            proc=proc,
            pgid=pgid,
            reason=str(exc),
            grace_s=grace_s,
            remote_execution=remote_execution,
        )
        return _with_flight_log(
            flight,
            record,
            RunResult(
                provider=provider,
                command=cmd,
                output="",
                exit_code=1,
                error=str(exc),
                duration_s=time.monotonic() - start,
                execution=record.to_dict(),
            ),
        )
    finally:
        observer.stop()


def _pty_poll_interval(record: ExecutionRecord, cap_s: float = 0.2) -> float:
    """Bound the select() wait so it wakes up in time for whichever deadline
    (absolute or idle) is closer, without polling faster than `cap_s`."""
    now = time.monotonic()
    remaining_candidates = [cap_s]
    if record.absolute_deadline_s is not None:
        remaining_candidates.append(
            record.absolute_deadline_s - (now - record.created_at_monotonic)
        )
    if record.idle_deadline_s is not None:
        idle_since = record.last_progress_at_monotonic or record.state_entered_at_monotonic
        remaining_candidates.append(record.idle_deadline_s - (now - idle_since))
    return max(0.0, min(remaining_candidates))


def run_with_pty(
    provider: str,
    command: Sequence[str],
    *,
    cwd: str | None = None,
    timeout: int = 300,
    idle_timeout: float | None = None,
    termination_grace_s: float | None = None,
    env: dict | None = None,
    execution_id: str | None = None,
    attempt_id: str | None = None,
    on_execution_update: Callable[[dict], None] | None = None,
    execution_control: ExecutionControl | None = None,
    remote_execution: bool = False,
    service_profile: str | None = None,
) -> RunResult:
    """Executa CLI que exige TTY (ex.: agy -p) via pseudo-terminal.

    Same two-deadline contract as run_subprocess(): `timeout` is an absolute
    ceiling, `idle_timeout` (optional) bounds time since the last PTY byte
    observed, and the absolute ceiling always wins/still applies even while
    idle_timeout keeps getting reset by incoming output.
    """
    if not HAS_PTY:
        return run_subprocess(
            provider,
            command,
            cwd=cwd,
            timeout=timeout,
            idle_timeout=idle_timeout,
            termination_grace_s=termination_grace_s,
            env=env,
            execution_id=execution_id,
            attempt_id=attempt_id,
            on_execution_update=on_execution_update,
            execution_control=execution_control,
            remote_execution=remote_execution,
            service_profile=service_profile,
        )

    cmd = list(command)
    merged_env = _enriched_env(env)
    merged_env.setdefault("TERM", "dumb")

    master_fd: int | None = None
    slave_fd: int | None = None
    proc: subprocess.Popen | None = None
    pgid: int | None = None
    start = time.monotonic()
    record_kwargs: dict[str, str] = {}
    if execution_id is not None:
        record_kwargs["execution_id"] = execution_id
    if attempt_id is not None:
        record_kwargs["attempt_id"] = attempt_id
    record = ExecutionRecord(
        provider=provider,
        profile=service_profile,
        transport="ssh" if remote_execution else "local",
        **record_kwargs,
    )
    flight = FlightRecorderSession(
        execution_id=record.execution_id,
        attempt_id=record.attempt_id,
        provider=provider,
        profile=service_profile,
        transport=record.transport,
        command=cmd,
        cwd=cwd,
        env=merged_env,
    )
    record.on_update = flight.wrap_on_update(on_execution_update)
    record.transition(ExecutionState.STARTING)
    grace_s = TERMINATION_GRACE_S if termination_grace_s is None else termination_grace_s
    if execution_control is not None and execution_control.cancellation_requested:
        reason = normalize_cancel_reason(execution_control.cancel_reason)
        if execution_control.client_abandoned:
            record.mark_client_abandoned()
            reason = "client_abandoned"
        record.transition(ExecutionState.CANCELLED, reason=reason)
        return _with_flight_log(
            flight,
            record,
            RunResult(
                provider=provider,
                command=cmd,
                output="",
                exit_code=130,
                timed_out=False,
                error=reason,
                duration_s=time.monotonic() - start,
                execution=record.to_dict(),
            ),
        )

    def _timeout_reason(deadline_reason: str) -> str:
        if deadline_reason == "absolute_deadline":
            return f"Timeout após {timeout}s"
        return f"Timeout de inatividade após {idle_timeout}s sem progresso observável"

    try:
        master_fd, slave_fd = pty.openpty()
        popen_kwargs: dict = {
            "stdin": slave_fd,
            "stdout": slave_fd,
            "stderr": slave_fd,
            "cwd": cwd,
            "close_fds": True,
            "env": merged_env,
        }
        if HAS_PROCESS_GROUPS:
            popen_kwargs["start_new_session"] = True
        proc = subprocess.Popen(cmd, **popen_kwargs)
        pgid: int | None = None
        if HAS_PROCESS_GROUPS:
            try:
                pgid = os.getpgid(proc.pid)
            except OSError:
                pgid = None
        record.set_process_identity(proc.pid, pgid)
        if remote_execution:
            record.mark_remote_session_started()
        record.transition(ExecutionState.RUNNING)
        record.configure_deadlines(
            absolute_deadline_s=float(timeout),
            idle_deadline_s=float(idle_timeout) if idle_timeout is not None else None,
            termination_grace_s=grace_s,
        )
        os.close(slave_fd)
        slave_fd = None

        observer = WorkspaceChangeObserver(
            workspace=cwd,
            record=record,
            flight=flight,
            poll_interval_s=0.5,
        )
        observer.start()

        chunks: list[bytes] = []
        cancel_reason = "user_requested"

        while True:
            if execution_control is not None and execution_control.cancellation_requested:
                cancel_reason = normalize_cancel_reason(execution_control.cancel_reason)
                if execution_control.client_abandoned:
                    record.mark_client_abandoned()
                    cancel_reason = "client_abandoned"
                raw = b"".join(chunks).decode("utf-8", errors="replace")
                cleaned = clean_cli_output(raw)
                return _with_flight_log(
                    flight,
                    record,
                    _cancel_result(
                        record=record,
                        proc=proc,
                        pgid=pgid,
                        reason=cancel_reason,
                        provider=provider,
                        cmd=cmd,
                        start=start,
                        stdout=cleaned,
                        stderr="",
                        output=cleaned,
                        grace_s=grace_s,
                        remote_execution=remote_execution,
                    ),
                    stdout=cleaned,
                    stderr="",
                )
            deadline_reason = record.deadline_status()
            if deadline_reason is not None:
                timeout_reason = _timeout_reason(deadline_reason)
                record.transition(ExecutionState.CANCELLATION_REQUESTED, reason=timeout_reason)
                record.transition(ExecutionState.TERMINATING, reason=timeout_reason)
                direct_confirmed, tree_confirmed = terminate_process_tree(
                    proc, pgid, grace_s=grace_s
                )
                if direct_confirmed:
                    record.confirm_direct_process_terminated()
                if tree_confirmed:
                    record.confirm_process_tree_terminated()
                remote_unconfirmed = (
                    remote_execution
                    and record.remote_session_started
                    and record.remote_termination_confirmed is not True
                )
                final_state = (
                    ExecutionState.TERMINATION_UNCONFIRMED
                    if remote_unconfirmed
                    else (
                        ExecutionState.CANCELLED
                        if tree_confirmed
                        else ExecutionState.TERMINATION_UNCONFIRMED
                    )
                )
                record.transition(final_state, reason=timeout_reason)
                raw = b"".join(chunks).decode("utf-8", errors="replace")
                cleaned = clean_cli_output(raw)
                return _with_flight_log(
                    flight,
                    record,
                    RunResult(
                        provider=provider,
                        command=cmd,
                        output=cleaned,
                        exit_code=124,
                        timed_out=True,
                        error=timeout_reason,
                        duration_s=time.monotonic() - start,
                        execution=record.to_dict(),
                    ),
                    stdout=cleaned,
                    stderr="",
                )

            ready, _, _ = select.select([master_fd], [], [], _pty_poll_interval(record))
            if ready:
                try:
                    data = os.read(master_fd, 65536)
                except OSError:
                    data = b""
                if not data:
                    break
                chunks.append(data)
                record.record_progress()

            if proc.poll() is not None:
                record.record_progress()  # subprocess completion is itself progress
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

        remaining = max(0.1, record.absolute_deadline_s - (time.monotonic() - record.created_at_monotonic))
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            deadline_reason = record.deadline_status() or "absolute_deadline"
            timeout_reason = _timeout_reason(deadline_reason)
            record.transition(ExecutionState.CANCELLATION_REQUESTED, reason=timeout_reason)
            record.transition(ExecutionState.TERMINATING, reason=timeout_reason)
            direct_confirmed, tree_confirmed = terminate_process_tree(proc, pgid, grace_s=grace_s)
            if direct_confirmed:
                record.confirm_direct_process_terminated()
            if tree_confirmed:
                record.confirm_process_tree_terminated()
            remote_unconfirmed = (
                remote_execution
                and record.remote_session_started
                and record.remote_termination_confirmed is not True
            )
            final_state = (
                ExecutionState.TERMINATION_UNCONFIRMED
                if remote_unconfirmed
                else (
                    ExecutionState.CANCELLED
                    if tree_confirmed
                    else ExecutionState.TERMINATION_UNCONFIRMED
                )
            )
            record.transition(final_state, reason=timeout_reason)
            raw = b"".join(chunks).decode("utf-8", errors="replace")
            cleaned = clean_cli_output(raw)
            return _with_flight_log(
                flight,
                record,
                RunResult(
                    provider=provider,
                    command=cmd,
                    output=cleaned,
                    exit_code=124,
                    timed_out=True,
                    error=timeout_reason,
                    duration_s=time.monotonic() - start,
                    execution=record.to_dict(),
                ),
                stdout=cleaned,
                stderr="",
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
        cleaned = clean_cli_output(raw)
        return _with_flight_log(
            flight,
            record,
            _finalize_after_natural_exit(
                record=record,
                proc=proc,
                pgid=pgid,
                grace_s=grace_s,
                provider=provider,
                cmd=cmd,
                start=start,
                stdout=cleaned,
                stderr="",
                output=cleaned,
            ),
            stdout=cleaned,
            stderr="",
        )
    except FileNotFoundError:
        # If this happened before Popen() created a process, this is a plain
        # pre-launch failure. If it happened after launch, run the same
        # termination/confirmation pipeline used for other runtime failures.
        if proc is None or not record.process_created:
            record.transition(ExecutionState.FAILED, reason="Comando não encontrado")
        else:
            _terminate_after_runtime_exception(
                record=record,
                proc=proc,
                pgid=pgid,
                reason="Comando não encontrado",
                grace_s=grace_s,
                remote_execution=remote_execution,
            )
        return _with_flight_log(
            flight,
            record,
            RunResult(
                provider=provider,
                command=cmd,
                output="",
                exit_code=127,
                error=f"Comando não encontrado: {cmd[0]}",
                duration_s=time.monotonic() - start,
                execution=record.to_dict(),
            ),
        )
    except OSError as exc:
        _terminate_after_runtime_exception(
            record=record,
            proc=proc,
            pgid=pgid,
            reason=str(exc),
            grace_s=grace_s,
            remote_execution=remote_execution,
        )
        return _with_flight_log(
            flight,
            record,
            RunResult(
                provider=provider,
                command=cmd,
                output="",
                exit_code=1,
                error=str(exc),
                duration_s=time.monotonic() - start,
                execution=record.to_dict(),
            ),
        )
    finally:
        observer.stop()
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
            # Safety net for exit paths above that didn't already tear the
            # process down (e.g. FileNotFoundError/OSError before launch
            # failed midway). Idempotent: a no-op if already terminated.
            terminate_process_tree(proc, pgid, grace_s=grace_s)
