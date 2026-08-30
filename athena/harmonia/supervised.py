"""Adaptador de execução com prazo limitado e cancelamento supervisionado."""

from __future__ import annotations

import subprocess
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar

from .contracts import (
    TERMINATION_WAIT_S,
    SubtaskExecutor,
    SubtaskSpec,
)

DEFAULT_SUBTASK_DEADLINE_S = 30.0


def is_cancellable_executor(executor: object) -> bool:
    return (
        callable(getattr(executor, "execute", None))
        and callable(getattr(executor, "cancel", None))
        and callable(getattr(executor, "wait_terminated", None))
    )


class ExecutionResultKind(str, Enum):
    COMPLETED = "completed"
    TIMEOUT = "timeout"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NON_CANCELLABLE = "non_cancellable"
    NON_TERMINATED = "non_terminated"


@dataclass(frozen=True, slots=True)
class SupervisedExecutionResult:
    kind: ExecutionResultKind
    exit_code: int
    altered_paths: tuple[str, ...] = ()
    error_message: str = ""


class InterruptibleSubprocessExecutor:
    """Executor base para testes — registra Popen para terminação em timeout."""

    _active: ClassVar[dict[str, subprocess.Popen[str]]] = {}
    _lock: ClassVar[threading.Lock] = threading.Lock()

    def cancel(self, *, attempt_id: str) -> None:
        with self._lock:
            proc = self._active.get(attempt_id)
        if proc is not None and proc.poll() is None:
            proc.kill()

    def wait_terminated(self, *, attempt_id: str, deadline_s: float) -> bool:
        deadline = time.monotonic() + deadline_s
        while time.monotonic() < deadline:
            with self._lock:
                proc = self._active.get(attempt_id)
            if proc is None:
                return True
            if proc.poll() is not None:
                with self._lock:
                    self._active.pop(attempt_id, None)
                return True
            time.sleep(0.01)
        return False

    @classmethod
    def terminate_all(cls) -> None:
        with cls._lock:
            items = list(cls._active.items())
            cls._active.clear()
        for _, proc in items:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=2)


class SynchronousCancellableExecutor:
    """Mixin para executores síncronos rápidos — término imediato após execute."""

    _finished: ClassVar[set[str]] = set()
    _lock: ClassVar[threading.Lock] = threading.Lock()

    def cancel(self, *, attempt_id: str) -> None:
        return

    def wait_terminated(self, *, attempt_id: str, deadline_s: float) -> bool:
        deadline = time.monotonic() + deadline_s
        while time.monotonic() < deadline:
            with self._lock:
                if attempt_id in self._finished:
                    return True
            time.sleep(0.001)
        with self._lock:
            return attempt_id in self._finished

    def _mark_finished(self, attempt_id: str) -> None:
        with self._lock:
            self._finished.add(attempt_id)

    @classmethod
    def clear_finished(cls) -> None:
        with cls._lock:
            cls._finished.clear()


class SupervisedExecutorAdapter:
    """Executa subtarefa com prazo limitado; exige executor cancelável."""

    def __init__(
        self,
        inner: SubtaskExecutor,
        *,
        default_deadline_s: float = DEFAULT_SUBTASK_DEADLINE_S,
        max_workers: int = 4,
        termination_wait_s: float = TERMINATION_WAIT_S,
    ) -> None:
        self._inner = inner
        self._default_deadline_s = default_deadline_s
        self._termination_wait_s = termination_wait_s
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="harmonia-sub"
        )
        self._active: dict[str, Future[tuple[int, tuple[str, ...]]]] = {}
        self._active_lock = threading.Lock()

    def is_cancellable(self) -> bool:
        return is_cancellable_executor(self._inner)

    def execute(
        self,
        *,
        subtask: SubtaskSpec,
        workspace_root: str,
        attempt_id: str,
        cancel_event: threading.Event | None = None,
    ) -> SupervisedExecutionResult:
        if not self.is_cancellable():
            return SupervisedExecutionResult(
                kind=ExecutionResultKind.NON_CANCELLABLE,
                exit_code=-1,
            )

        deadline = (
            subtask.deadline_s
            if subtask.deadline_s is not None
            else self._default_deadline_s
        )
        if cancel_event is not None and cancel_event.is_set():
            cancellable = self._inner  # type: ignore[assignment]
            cancellable.cancel(attempt_id=attempt_id)
            if not cancellable.wait_terminated(
                attempt_id=attempt_id, deadline_s=self._termination_wait_s
            ):
                return SupervisedExecutionResult(
                    kind=ExecutionResultKind.NON_TERMINATED,
                    exit_code=-1,
                )
            return SupervisedExecutionResult(
                kind=ExecutionResultKind.CANCELLED,
                exit_code=-1,
            )

        future = self._pool.submit(
            self._inner.execute,
            subtask=subtask,
            workspace_root=workspace_root,
            attempt_id=attempt_id,
        )
        with self._active_lock:
            self._active[attempt_id] = future

        cancellable = self._inner  # type: ignore[assignment]
        timed_out = False
        cancelled = False
        exit_code = 1
        altered: tuple[str, ...] = ()

        try:
            exit_code, altered = future.result(timeout=deadline)
        except FuturesTimeoutError:
            timed_out = True
            cancellable.cancel(attempt_id=attempt_id)
        except Exception as exc:  # noqa: BLE001 — executor faults must not abort peers
            return SupervisedExecutionResult(
                kind=ExecutionResultKind.FAILED,
                exit_code=1,
                error_message=str(exc),
            )
        finally:
            with self._active_lock:
                self._active.pop(attempt_id, None)

        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            cancellable.cancel(attempt_id=attempt_id)

        if timed_out or cancelled:
            if not cancellable.wait_terminated(
                attempt_id=attempt_id, deadline_s=self._termination_wait_s
            ):
                return SupervisedExecutionResult(
                    kind=ExecutionResultKind.NON_TERMINATED,
                    exit_code=-1,
                )
            if timed_out:
                return SupervisedExecutionResult(
                    kind=ExecutionResultKind.TIMEOUT,
                    exit_code=-1,
                )
            return SupervisedExecutionResult(
                kind=ExecutionResultKind.CANCELLED,
                exit_code=-1,
                altered_paths=altered,
            )

        return SupervisedExecutionResult(
            kind=ExecutionResultKind.COMPLETED,
            exit_code=exit_code,
            altered_paths=altered,
        )

    def cancel_attempt(self, attempt_id: str) -> None:
        if is_cancellable_executor(self._inner):
            self._inner.cancel(attempt_id=attempt_id)  # type: ignore[union-attr]

    def wait_attempt_terminated(
        self, attempt_id: str, *, deadline_s: float | None = None
    ) -> bool:
        if not is_cancellable_executor(self._inner):
            return True
        return self._inner.wait_terminated(  # type: ignore[union-attr]
            attempt_id=attempt_id,
            deadline_s=deadline_s or self._termination_wait_s,
        )

    def shutdown(self, *, timeout_s: float = 5.0) -> None:
        if isinstance(self._inner, InterruptibleSubprocessExecutor):
            InterruptibleSubprocessExecutor.terminate_all()
        if isinstance(self._inner, SynchronousCancellableExecutor):
            SynchronousCancellableExecutor.clear_finished()
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            with self._active_lock:
                pending = list(self._active.values())
            if not pending:
                break
            for future in pending:
                future.cancel()
            time.sleep(0.01)
        self._pool.shutdown(wait=True, cancel_futures=True)
