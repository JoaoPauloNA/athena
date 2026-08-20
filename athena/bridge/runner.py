"""Runner local de subprocesso e PTY da ponte Athena."""

from __future__ import annotations

import os
import select
import subprocess
import threading
import time
from pathlib import Path
from typing import BinaryIO

from athena.execution import (
    DeadlineKind,
    ExecutionControl,
    ExecutionRecord,
    ExecutionState,
)
from athena.lease import DirectoryLeaseContract

from .contracts import RunRequest, RunResult
from .posix import (
    observe_descendants,
    pid_may_be_alive,
    process_group_is_empty,
    terminate_owned_group,
)

if os.name == "posix":
    import pty

_POLL_INTERVAL_S = 0.02
_READER_JOIN_S = 0.25


def _read_pipe(stream: BinaryIO, chunks: list[bytes], execution: ExecutionRecord) -> None:
    try:
        while data := stream.read1(4096):
            chunks.append(data)
            execution.record_progress()
    except (OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _read_pty(fd: int, chunks: list[bytes], execution: ExecutionRecord) -> None:
    try:
        while True:
            readable, _, _ = select.select((fd,), (), (), 0.1)
            if not readable:
                continue
            try:
                data = os.read(fd, 4096)
            except OSError:
                break
            if not data:
                break
            chunks.append(data)
            execution.record_progress()
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _decode(chunks: list[bytes]) -> str:
    return b"".join(chunks).decode("utf-8", errors="replace")


class LocalBridgeRunner:
    """Executar comandos locais sob lease, deadlines e teardown controlado."""

    def run(
        self,
        request: RunRequest,
        execution: ExecutionRecord,
        lease: DirectoryLeaseContract,
        *,
        control: ExecutionControl | None = None,
    ) -> RunResult:
        """Dirigir QUEUED -> STARTING -> RUNNING -> terminal."""
        started = time.monotonic()
        command, cwd = self._validate(request, execution)
        acquired = False
        process: subprocess.Popen[bytes] | None = None
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        readers: list[threading.Thread] = []
        master_fd: int | None = None
        slave_fd: int | None = None
        expired: DeadlineKind | None = None
        error: str | None = None

        try:
            cwd = lease.acquire(
                cwd,
                execution.execution_id,
                execution.attempt_id,
                timeout=request.lease_timeout_s,
            )
            acquired = True
            if control is not None and control.cancellation_requested:
                execution.transition(ExecutionState.CANCELLED, reason=control.cancel_reason)
                return self._result(
                    command, cwd, execution, None, stdout_chunks, stderr_chunks, started
                )
            expired = execution.expired_deadline()
            if expired is not None:
                execution.transition(ExecutionState.TIMED_OUT)
                return self._result(
                    command,
                    cwd,
                    execution,
                    None,
                    stdout_chunks,
                    stderr_chunks,
                    started,
                    expired=expired,
                )

            execution.transition(ExecutionState.STARTING)
            environment = os.environ.copy()
            environment.update(request.env)
            environment["PWD"] = str(cwd)
            popen_kwargs: dict[str, object] = {
                "cwd": cwd,
                "env": environment,
                "start_new_session": os.name == "posix",
            }
            if request.use_pty:
                if os.name != "posix":
                    raise OSError("PTY is only available on POSIX platforms")
                master_fd, slave_fd = pty.openpty()
                popen_kwargs.update(stdout=slave_fd, stderr=slave_fd, stdin=subprocess.DEVNULL)
            else:
                popen_kwargs.update(
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    stdin=subprocess.DEVNULL,
                )
            process = subprocess.Popen(command, **popen_kwargs)  # type: ignore[arg-type]
            if slave_fd is not None:
                os.close(slave_fd)
                slave_fd = None

            execution.transition(ExecutionState.RUNNING)
            if master_fd is not None:
                reader = threading.Thread(
                    target=_read_pty,
                    args=(master_fd, stdout_chunks, execution),
                    daemon=True,
                )
                master_fd = None
                readers.append(reader)
            else:
                assert process.stdout is not None and process.stderr is not None
                readers.extend(
                    (
                        threading.Thread(
                            target=_read_pipe,
                            args=(process.stdout, stdout_chunks, execution),
                            daemon=True,
                        ),
                        threading.Thread(
                            target=_read_pipe,
                            args=(process.stderr, stderr_chunks, execution),
                            daemon=True,
                        ),
                    )
                )
            for reader in readers:
                reader.start()

            escaped_seen: set[int] = set()
            descendants_seen: set[int] = set()
            process_group = process.pid if os.name == "posix" else None
            while True:
                if process_group is not None:
                    descendants_seen, escaped_now = observe_descendants(
                        process.pid,
                        process_group,
                        descendants_seen,
                    )
                    escaped_seen.update(escaped_now)
                return_code = process.poll()
                if return_code is not None:
                    break
                if control is not None and control.cancellation_requested:
                    error = control.cancel_reason or "cancelled"
                    break
                expired = execution.expired_deadline()
                if expired is not None:
                    error = expired.value
                    break
                time.sleep(_POLL_INTERVAL_S)

            termination_requested = error is not None
            direct_confirmed = process.poll() is not None
            group_confirmed = process_group is None
            if process_group is not None:
                if termination_requested or not process_group_is_empty(process_group):
                    direct_confirmed, group_confirmed = terminate_owned_group(
                        process,
                        process_group,
                        grace_s=request.termination_grace_s,
                    )
                else:
                    group_confirmed = True

            reader_deadline = time.monotonic() + 0.1
            for reader in readers:
                reader.join(timeout=max(0.0, reader_deadline - time.monotonic()))
            streams_closed = all(not reader.is_alive() for reader in readers)
            escaped_alive = {pid for pid in escaped_seen if pid_may_be_alive(pid)}
            termination_confirmed = (
                direct_confirmed
                and group_confirmed
                and not escaped_alive
                and streams_closed
            )
            if not termination_confirmed:
                execution.transition(ExecutionState.TERMINATION_UNCONFIRMED)
            elif expired is not None:
                execution.transition(ExecutionState.TIMED_OUT)
            elif control is not None and control.cancellation_requested:
                execution.transition(ExecutionState.CANCELLED, reason=control.cancel_reason)
            elif process.returncode == 0:
                execution.transition(ExecutionState.COMPLETED)
            else:
                execution.transition(ExecutionState.FAILED)

        except (OSError, TimeoutError) as exc:
            error = str(exc)
            if process is not None and process.poll() is None:
                if os.name == "posix":
                    terminate_owned_group(
                        process,
                        process.pid,
                        grace_s=request.termination_grace_s,
                    )
                else:
                    process.kill()
                    process.wait()
            if execution.state in (ExecutionState.QUEUED, ExecutionState.STARTING):
                execution.transition(ExecutionState.FAILED)
            elif execution.state is ExecutionState.RUNNING:
                execution.transition(ExecutionState.TERMINATION_UNCONFIRMED)
        finally:
            if slave_fd is not None:
                os.close(slave_fd)
            if master_fd is not None:
                os.close(master_fd)
            for reader in readers:
                reader.join(timeout=_READER_JOIN_S)
            if acquired:
                lease.release(cwd, execution.execution_id, execution.attempt_id)

        return self._result(
            command,
            cwd,
            execution,
            process,
            stdout_chunks,
            stderr_chunks,
            started,
            expired=expired,
            error=error,
        )

    @staticmethod
    def _validate(
        request: RunRequest, execution: ExecutionRecord
    ) -> tuple[tuple[str, ...], Path]:
        if execution.state is not ExecutionState.QUEUED:
            raise ValueError("execution must be in queued state")
        command = tuple(request.command)
        if not command or any(not isinstance(item, str) or not item for item in command):
            raise ValueError("command must contain non-empty strings")
        cwd = Path(request.cwd).resolve()
        if not cwd.is_dir():
            raise ValueError("cwd must be an existing directory")
        if request.termination_grace_s < 0:
            raise ValueError("termination_grace_s must be non-negative")
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in request.env.items()):
            raise TypeError("environment keys and values must be strings")
        return command, cwd

    @staticmethod
    def _result(
        command: tuple[str, ...],
        cwd: Path,
        execution: ExecutionRecord,
        process: subprocess.Popen[bytes] | None,
        stdout: list[bytes],
        stderr: list[bytes],
        started: float,
        *,
        expired: DeadlineKind | None = None,
        error: str | None = None,
    ) -> RunResult:
        return RunResult(
            command=command,
            cwd=cwd,
            state=execution.state,
            exit_code=None if process is None else process.returncode,
            stdout=_decode(stdout),
            stderr=_decode(stderr),
            duration_s=time.monotonic() - started,
            expired_deadline=expired,
            error=error,
        )
