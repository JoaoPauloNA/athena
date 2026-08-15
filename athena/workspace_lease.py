"""In-process workspace lease (Sprint A6).

Serializes execution against a *canonical workspace path* so two attempts —
whether from two different `router.run_combo()` calls or a fallback/retry
inside the same one — never run concurrently against the same working
directory. Model: canonical workspace -> owning execution_id -> active
attempt_id.

LIMITATION (documented here and in tests, not in public docs): this registry
lives in this process's memory only (`threading.Lock` + a plain dict). It
gives no protection at all between separate OS processes, workers, or hosts
sharing the same filesystem — only concurrent callers inside the *same*
Python process/interpreter are serialized. Sprint A6 scope is explicitly
in-process only; a cross-process lock (file lock, DB row, broker) is future
work, not attempted here.

This module knows nothing about subprocess/CLI mechanics. It is handed
execution lifecycle metadata (the `RunResult.execution` dict produced by
`athena.execution.ExecutionRecord.to_dict()`) and decides, from that alone,
whether it is safe to release or transfer a lease — mirroring (but not
sharing code with) the conservative policy in
`athena.router._fallback_block_reason`, tailored for lease release rather
than fallback permission (see `_is_safe_to_release` for where the two
intentionally diverge, e.g. COMPLETED).
"""
from __future__ import annotations

import os
import threading

from athena.execution import ExecutionState

_TERMINAL_ATTEMPT_STATES = frozenset(
    {
        ExecutionState.FAILED.value,
        ExecutionState.CANCELLED.value,
        ExecutionState.TIMED_OUT.value,
    }
)


class WorkspaceLeaseError(Exception):
    """Raised when a workspace lease cannot be acquired/transferred safely."""


class _LeaseEntry:
    __slots__ = ("execution_id", "attempt_id")

    def __init__(self, execution_id: str, attempt_id: str) -> None:
        self.execution_id = execution_id
        self.attempt_id = attempt_id


_lock = threading.Lock()
_leases: dict[str, _LeaseEntry] = {}


def canonicalize_workspace(path: str) -> str:
    """Resolve `path` to its real, absolute form.

    `os.path.realpath` both makes relative paths absolute (relative to cwd)
    and resolves symlinks, so two differently-spelled paths that name the
    same directory on disk (via a symlink, `..` segments, or a trailing
    slash) collide on the same lease key.
    """
    return os.path.realpath(path)


def _is_safe_to_release(execution_meta: dict | None) -> bool:
    """Decide, from `RunResult.execution` metadata alone, whether the
    attempt that produced it is safely over within Athena's owned termination
    scope. On POSIX, this is the owned process group plus positive detection
    of currently visible escapes; it is not universal OS containment.

    - No metadata at all: never safe (fail-closed). Under active lease
      lifecycle, releasing without execution metadata could silently drop a
      lock while the attempt status is unknown.
    - `client_abandoned`: by itself does not prove the process ended and does
      not block release if termination is otherwise confirmed.
    - `TERMINATION_UNCONFIRMED`: never safe by definition.
    - `COMPLETED` with `process_created=False`: safe for logical phases that
      never spawned OS processes.
    - `COMPLETED` otherwise: safe only when direct process termination is confirmed
      and, if a process group existed, tree termination is also confirmed.
      A plain successful direct exit is insufficient if descendants may still
      be alive in the owned group.
    - `FAILED` with `process_created` explicitly False: safe — Popen() never
      created a process (e.g. missing command), so there is nothing to have
      terminated.
    - `FAILED`/`CANCELLED`/`TIMED_OUT` otherwise: safe only once the direct
      process (and, when a process group existed, the whole tree) is
      positively confirmed terminated.
    - Any other state (QUEUED/STARTING/RUNNING/CANCELLATION_REQUESTED/
      TERMINATING): never safe — the attempt may still be active.
    """
    if not execution_meta:
        return False

    state = execution_meta.get("state")
    if (
        execution_meta.get("remote_session_started") is True
        and execution_meta.get("remote_termination_confirmed") is not True
    ):
        return False

    if state == ExecutionState.TERMINATION_UNCONFIRMED.value:
        return False

    if state == ExecutionState.COMPLETED.value:
        if execution_meta.get("process_created") is False:
            return True
        if not execution_meta.get("direct_process_terminated_confirmed"):
            return False
        if execution_meta.get("pgid") is not None and not execution_meta.get(
            "process_tree_terminated_confirmed"
        ):
            return False
        return True

    if state in _TERMINAL_ATTEMPT_STATES:
        if state == ExecutionState.CANCELLED.value and execution_meta.get("process_created") is False:
            return True
        if state == ExecutionState.FAILED.value and execution_meta.get("process_created") is False:
            return True
        if state == ExecutionState.TIMED_OUT.value and execution_meta.get("process_created") is False:
            return True
        if not execution_meta.get("direct_process_terminated_confirmed"):
            return False
        if execution_meta.get("pgid") is not None and not execution_meta.get(
            "process_tree_terminated_confirmed"
        ):
            return False
        return True

    return False


def is_safe_attempt_end(
    execution_meta: dict | None,
    expected_execution_id: str | None = None,
    expected_attempt_id: str | None = None,
) -> bool:
    """Public helper: fail-closed check for a phase/attempt end.

    Returns a plain bool only (no exceptions, no path handling):
    - metadata must exist;
    - if expected IDs are provided, they must match;
    - underlying termination confirmation policy must pass.
    """
    if not execution_meta:
        return False
    if (
        expected_execution_id is not None
        and execution_meta.get("execution_id") != expected_execution_id
    ):
        return False
    if (
        expected_attempt_id is not None
        and execution_meta.get("attempt_id") != expected_attempt_id
    ):
        return False
    return _is_safe_to_release(execution_meta)


def acquire(workspace: str, execution_id: str, attempt_id: str) -> None:
    """Acquire the lease on `workspace` for (execution_id, attempt_id).

    Idempotent ONLY for the exact same (execution_id, attempt_id) pair —
    re-acquiring with both unchanged just re-affirms the active attempt,
    safe to call more than once (e.g. a caller re-affirming right before a
    provider call). A *different* `execution_id` trying to acquire an
    already-held workspace raises `WorkspaceLeaseError`, and so does the
    SAME `execution_id` trying to acquire with a *different* `attempt_id`:
    silently overwriting the active attempt here would drop the prior
    attempt's identity without ever consulting `_is_safe_to_release`,
    bypassing the exact safety check `transfer()` exists to enforce.
    Callers moving to a new attempt of the same execution must call
    `transfer()` instead, which requires `execution_meta` confirming the
    prior attempt is safely over. A different, unheld workspace is always
    free to acquire.
    """
    acquire_for_scope(workspace, execution_id, attempt_id)


def acquire_for_scope(workspace: str, execution_id: str, attempt_id: str) -> bool:
    """Atomically acquire lease ownership for this exact scope.

    Returns:
      - True: the workspace was free and this call acquired it (caller owns).
      - False: the workspace was already held by the exact same
        (execution_id, attempt_id) pair (reentrant scope; ownership pre-existed).

    Raises:
      - WorkspaceLeaseError for conservative conflicts, with the same semantics
        and messages as acquire().
    """
    key = canonicalize_workspace(workspace)
    with _lock:
        current = _leases.get(key)
        if current is None:
            _leases[key] = _LeaseEntry(execution_id, attempt_id)
            return True
        if current.execution_id != execution_id:
            raise WorkspaceLeaseError(
                f"Workspace '{key}' já está em uso pela execução {current.execution_id} "
                f"(tentativa {current.attempt_id}); não é seguro iniciar a execução "
                f"{execution_id} concorrentemente no mesmo workspace."
            )
        if current.attempt_id != attempt_id:
            raise WorkspaceLeaseError(
                f"Workspace '{key}' já está em uso pela execução {current.execution_id} "
                f"(tentativa {current.attempt_id}); use transfer() para mover o lease "
                f"para a nova tentativa {attempt_id} após confirmar a terminação segura "
                "da tentativa anterior."
            )
        return False


def transfer(
    workspace: str,
    execution_id: str,
    from_attempt_id: str,
    new_attempt_id: str,
    *,
    execution_meta: dict | None,
) -> None:
    """Move the lease on `workspace`, still held by `execution_id`, to a new
    attempt — the fallback/retry path within the SAME execution.

    Only allowed once `execution_meta` (the prior attempt's
    `RunResult.execution`) positively confirms that attempt is safely over
    (see `_is_safe_to_release`); otherwise raises `WorkspaceLeaseError`,
    since the prior process might still be running against this workspace.
    Also raises if `execution_id` is not the current holder — there is
    nothing of its to transfer.
    """
    key = canonicalize_workspace(workspace)
    with _lock:
        current = _leases.get(key)
        if current is None or current.execution_id != execution_id:
            raise WorkspaceLeaseError(
                f"Não é possível transferir o lease de '{key}': a execução {execution_id} "
                "não é a titular atual."
            )
        if current.attempt_id != from_attempt_id:
            raise WorkspaceLeaseError(
                f"Não é possível transferir o lease de '{key}': a tentativa titular atual "
                f"é {current.attempt_id}, não {from_attempt_id}."
            )
        if not is_safe_attempt_end(
            execution_meta,
            expected_execution_id=execution_id,
            expected_attempt_id=from_attempt_id,
        ):
            raise WorkspaceLeaseError(
                f"Não é seguro transferir o lease de '{key}' para uma nova tentativa: "
                "metadados ausentes/divergentes ou terminação da tentativa anterior "
                "não confirmada."
            )
        current.attempt_id = new_attempt_id


def release(workspace: str, execution_id: str, attempt_id: str) -> None:
    """Unconditionally release the lease on `workspace` if held by
    `execution_id`.

    Idempotent: releasing an already-released/never-acquired workspace, or
    one held by a different (execution_id, attempt_id) pair, is a safe no-op
    — callers never accidentally release someone else's/current attempt lease.
    """
    key = canonicalize_workspace(workspace)
    with _lock:
        current = _leases.get(key)
        if (
            current is not None
            and current.execution_id == execution_id
            and current.attempt_id == attempt_id
        ):
            del _leases[key]


def release_after_attempt(
    workspace: str,
    execution_id: str,
    attempt_id: str,
    *,
    execution_meta: dict | None,
) -> bool:
    """Release the lease on `workspace` (held by `execution_id`) only if
    `execution_meta` shows it is safe to do so; otherwise retain it
    untouched. Returns whether it released.

    This is the policy-aware counterpart to `release()`: callers who know
    they are permanently done with the workspace (no possible transfer)
    should use this instead of `release()` directly, so a still-active or
    unconfirmed-termination attempt never gets its lease dropped.
    """
    key = canonicalize_workspace(workspace)
    with _lock:
        current = _leases.get(key)
        if current is None:
            return _is_safe_to_release(execution_meta)
        if current.execution_id != execution_id or current.attempt_id != attempt_id:
            return False
        if not is_safe_attempt_end(
            execution_meta,
            expected_execution_id=execution_id,
            expected_attempt_id=attempt_id,
        ):
            return False
        del _leases[key]
        return True
def current_holder(workspace: str) -> tuple[str, str] | None:
    """Return `(execution_id, attempt_id)` currently holding `workspace`'s
    lease, or None if unheld. Test/diagnostic helper."""
    key = canonicalize_workspace(workspace)
    with _lock:
        current = _leases.get(key)
        if current is None:
            return None
        return (current.execution_id, current.attempt_id)


def _reset_for_tests() -> None:
    """Test-only escape hatch: clears every held lease.

    Never called from production code paths — the in-process registry is
    otherwise expected to live for the process's whole lifetime.
    """
    with _lock:
        _leases.clear()
