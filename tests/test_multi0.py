"""Testes MULTI-0 — Harmonia, leases granulares e E2E controlado."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from athena.harmonia import (
    DenyWorktreeAuthority,
    HarmoniaEngine,
    HarmoniaError,
    IsolationStrategy,
    ResourceBudget,
    SubtaskSpec,
    SubtaskState,
    SyntheticGitWorktreeAuthority,
    TeamPlan,
    build_execution_plan,
    parse_team_plan,
)
from athena.harmonia.authorization import DeterministicFakeAuthorizer
from athena.harmonia.contracts import (
    REASON_BUSY,
    REASON_CYCLE,
    REASON_DUPLICATE_ID,
    REASON_EXECUTOR_NON_CANCELLABLE,
    REASON_INVALID_PLAN,
    REASON_MISSING_DEPENDENCY,
    REASON_OUT_OF_SCOPE,
    REASON_SUBTASK_CANCELLED,
    REASON_SUBTASK_FAILED,
    REASON_SUBTASK_TIMEOUT,
    REASON_UNAUTHORIZED,
    REASON_WORKTREE_CLEANUP_FAILED,
    REASON_WORKTREE_DENIED,
)
from athena.harmonia.paths import canonicalize_scope
from athena.harmonia.strategy import choose_isolation
from athena.harmonia.supervised import (
    InterruptibleSubprocessExecutor,
    SynchronousCancellableExecutor,
)
from athena.lease import (
    AccessMode,
    LeaseAcquisitionTimeout,
    LeaseOwnershipError,
    ResourceLeaseManager,
    ResourceOwner,
    ResourceRequest,
)
from athena.lease.resource import WORKSPACE_GLOBAL_KEY
from athena.mcp_server.server import TOOL_NAMES
from harness import benchmark_harmonia as harmonia_benchmark

_BUDGET = ResourceBudget(1, 64, 0, 1)
_AUTHORIZER = DeterministicFakeAuthorizer()


def _seal(
    *,
    task_id: str,
    subtask: SubtaskSpec,
    workspace_root: str,
    isolation: IsolationStrategy | None = None,
) -> str:
    root = Path(os.path.realpath(workspace_root))
    read_paths = tuple(
        str(p.relative_to(root))
        for p in canonicalize_scope(subtask.read_scope, workspace_root=str(root))
    )
    write_paths = tuple(
        str(p.relative_to(root))
        for p in canonicalize_scope(subtask.write_scope, workspace_root=str(root))
    )
    uncertain = not write_paths and subtask.operation_type == "file_edit"
    resolved_isolation = isolation or choose_isolation(
        subtask,
        write_paths=canonicalize_scope(subtask.write_scope, workspace_root=str(root)),
        uncertain_scope=uncertain,
    )
    return DeterministicFakeAuthorizer.seal_for(
        task_id=task_id,
        subtask=subtask,
        read_paths=read_paths,
        write_paths=write_paths,
        isolation=resolved_isolation,
    )


def _subtask(
    subtask_id: str,
    *,
    workspace_root: str = "/tmp",
    task_id: str = "task-001",
    dependencies: tuple[str, ...] = (),
    write_scope: tuple[str, ...] | None = None,
    read_scope: tuple[str, ...] = (),
    operation_type: str = "file_edit",
    worker_id: str = "worker-a",
    deadline_s: float | None = None,
    parallel: bool = False,
) -> SubtaskSpec:
    resolved_write = write_scope if write_scope is not None else (f"{subtask_id}.txt",)
    draft = SubtaskSpec(
        subtask_id=subtask_id,
        dependencies=dependencies,
        worker_id=worker_id,
        read_scope=read_scope,
        write_scope=resolved_write,
        operation_type=operation_type,
        resources=_BUDGET,
        seal_hash="0" * 64,
        deadline_s=deadline_s,
    )
    write_paths = canonicalize_scope(resolved_write, workspace_root=workspace_root)
    uncertain = not write_paths and operation_type == "file_edit"
    isolation = choose_isolation(
        draft,
        write_paths=write_paths,
        uncertain_scope=uncertain,
        parallel_writers=parallel,
    )
    seal = _seal(
        task_id=task_id,
        subtask=draft,
        workspace_root=workspace_root,
        isolation=isolation,
    )
    return SubtaskSpec(
        subtask_id=subtask_id,
        dependencies=dependencies,
        worker_id=worker_id,
        read_scope=read_scope,
        write_scope=resolved_write,
        operation_type=operation_type,
        resources=_BUDGET,
        seal_hash=seal,
        deadline_s=deadline_s,
    )


def _plan(
    *subtasks: SubtaskSpec,
    max_parallelism: int = 4,
    workspace_root: str = "/tmp",
    parallel: bool = False,
) -> TeamPlan:
    sealed = tuple(
        _subtask(
            s.subtask_id,
            workspace_root=workspace_root,
            dependencies=s.dependencies,
            write_scope=s.write_scope,
            read_scope=s.read_scope,
            operation_type=s.operation_type,
            worker_id=s.worker_id,
            deadline_s=s.deadline_s,
            parallel=parallel,
        )
        for s in subtasks
    )
    return TeamPlan(
        task_id="task-001",
        subtasks=sealed,
        max_parallelism=max_parallelism,
        project_parallelism=max_parallelism,
        aegis_parallelism=max_parallelism,
    )


def _engine(tmp_path: Path, executor, **kwargs) -> HarmoniaEngine:
    engine = HarmoniaEngine(
        workspace_root=str(tmp_path),
        executor=executor,
        seal_authorizer=_AUTHORIZER,
        **kwargs,
    )
    _ENGINES.append(engine)
    return engine


# ---------------------------------------------------------------------------
# Schemas and DAG
# ---------------------------------------------------------------------------


_ENGINES: list[HarmoniaEngine] = []


@pytest.fixture(autouse=True)
def _reset_harmonia_executor_state() -> None:
    SynchronousCancellableExecutor.clear_finished()
    yield
    while _ENGINES:
        engine = _ENGINES.pop()
        engine.shutdown(timeout_s=2.0)
    SynchronousCancellableExecutor.clear_finished()


def test_closed_plan_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError, match="HARMONIA_INVALID_PLAN"):
        parse_team_plan({"task_id": "t", "subtasks": [], "extra": 1})


def test_dag_rejects_cycle_and_duplicate_and_missing() -> None:
    plan = _plan(
        _subtask("a"),
        _subtask("b", dependencies=("a",)),
        _subtask("a"),
    )
    with pytest.raises(HarmoniaError) as caught:
        build_execution_plan(plan, workspace_root="/tmp", max_workers=4)
    assert REASON_DUPLICATE_ID in caught.value.reason_codes

    plan = _plan(_subtask("a", dependencies=("missing",)))
    with pytest.raises(HarmoniaError) as caught:
        build_execution_plan(plan, workspace_root="/tmp", max_workers=4)
    assert REASON_MISSING_DEPENDENCY in caught.value.reason_codes

    plan = _plan(
        _subtask("a", dependencies=("b",)),
        _subtask("b", dependencies=("a",)),
    )
    with pytest.raises(HarmoniaError) as caught:
        build_execution_plan(plan, workspace_root="/tmp", max_workers=4)
    assert REASON_CYCLE in caught.value.reason_codes


def test_execution_plan_is_deterministic(tmp_path: Path) -> None:
    plan = _plan(
        _subtask("c", dependencies=("a", "b"), workspace_root=str(tmp_path)),
        _subtask("a", workspace_root=str(tmp_path)),
        _subtask("b", dependencies=("a",), workspace_root=str(tmp_path)),
        _subtask("d", workspace_root=str(tmp_path)),
        workspace_root=str(tmp_path),
    )
    first = build_execution_plan(plan, workspace_root=str(tmp_path), max_workers=4)
    second = build_execution_plan(plan, workspace_root=str(tmp_path), max_workers=4)
    assert first == second
    assert [group.subtask_ids for group in first.groups] == [
        ("a", "d"),
        ("b",),
        ("c",),
    ]


def test_planner_serializes_same_file_and_global(tmp_path: Path) -> None:
    shared = _plan(
        _subtask("a", write_scope=("shared.txt",), workspace_root=str(tmp_path)),
        _subtask("b", write_scope=("shared.txt",), workspace_root=str(tmp_path)),
        workspace_root=str(tmp_path),
    )
    shared_plan = build_execution_plan(
        shared, workspace_root=str(tmp_path), max_workers=4
    )
    assert [group.subtask_ids for group in shared_plan.groups] == [("a",), ("b",)]

    global_plan = _plan(
        _subtask("a", operation_type="git", write_scope=(), workspace_root=str(tmp_path)),
        _subtask("b", operation_type="git", write_scope=(), workspace_root=str(tmp_path)),
        workspace_root=str(tmp_path),
    )
    execution = build_execution_plan(
        global_plan, workspace_root=str(tmp_path), max_workers=4
    )
    assert [group.subtask_ids for group in execution.groups] == [("a",), ("b",)]


# ---------------------------------------------------------------------------
# Resource leases — adversarial path conflicts
# ---------------------------------------------------------------------------


def test_resource_lease_atomic_all_or_nothing(tmp_path: Path) -> None:
    manager = ResourceLeaseManager(default_ttl_s=2.0)
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_text("a", encoding="utf-8")
    second.write_text("b", encoding="utf-8")
    owner_a = ResourceOwner("task", "sub-a", "attempt-a")
    owner_b = ResourceOwner("task", "sub-b", "attempt-b")
    req_a = ResourceRequest(
        key=manager.resource_key_for_path(first, mode=AccessMode.WRITE),
        mode=AccessMode.WRITE,
        path=manager.canonicalize_path(first),
    )
    req_b = ResourceRequest(
        key=manager.resource_key_for_path(second, mode=AccessMode.WRITE),
        mode=AccessMode.WRITE,
        path=manager.canonicalize_path(second),
    )
    manager.acquire_all((req_a,), owner_a)
    acquired = threading.Event()

    def compete() -> None:
        try:
            manager.acquire_all((req_a, req_b), owner_b, timeout=0.05)
        except LeaseAcquisitionTimeout:
            acquired.set()

    thread = threading.Thread(target=compete)
    thread.start()
    assert acquired.wait(timeout=1.0)
    manager.release_all((req_a,), owner_a)
    thread.join(timeout=1.0)


def test_directory_write_conflicts_with_descendant_read(tmp_path: Path) -> None:
    manager = ResourceLeaseManager(default_ttl_s=2.0)
    root = tmp_path / "tree"
    child = root / "child.txt"
    root.mkdir()
    child.write_text("x", encoding="utf-8")
    owner_a = ResourceOwner("t", "dir", "1")
    owner_b = ResourceOwner("t", "file", "1")
    dir_req = ResourceRequest(
        key=manager.resource_key_for_path(root, mode=AccessMode.WRITE),
        mode=AccessMode.WRITE,
        path=manager.canonicalize_path(root),
    )
    file_req = ResourceRequest(
        key=manager.resource_key_for_path(child, mode=AccessMode.READ),
        mode=AccessMode.READ,
        path=manager.canonicalize_path(child),
    )
    manager.acquire_all((dir_req,), owner_a)
    with pytest.raises(LeaseAcquisitionTimeout):
        manager.acquire_all((file_req,), owner_b, timeout=0.02)


def test_file_write_conflicts_with_ancestor_directory_write(tmp_path: Path) -> None:
    manager = ResourceLeaseManager(default_ttl_s=2.0)
    root = tmp_path / "pkg"
    target = root / "module.py"
    root.mkdir()
    target.write_text("x", encoding="utf-8")
    owner_a = ResourceOwner("t", "file", "1")
    owner_b = ResourceOwner("t", "dir", "1")
    file_req = ResourceRequest(
        key=manager.resource_key_for_path(target, mode=AccessMode.WRITE),
        mode=AccessMode.WRITE,
        path=manager.canonicalize_path(target),
    )
    dir_req = ResourceRequest(
        key=manager.resource_key_for_path(root, mode=AccessMode.WRITE),
        mode=AccessMode.WRITE,
        path=manager.canonicalize_path(root),
    )
    manager.acquire_all((file_req,), owner_a)
    with pytest.raises(LeaseAcquisitionTimeout):
        manager.acquire_all((dir_req,), owner_b, timeout=0.02)


def test_prefix_lookalike_paths_do_not_false_conflict(tmp_path: Path) -> None:
    manager = ResourceLeaseManager(default_ttl_s=2.0)
    alpha = tmp_path / "foo"
    beta = tmp_path / "foo_bar.txt"
    alpha.mkdir()
    beta.write_text("x", encoding="utf-8")
    owner_a = ResourceOwner("t", "a", "1")
    owner_b = ResourceOwner("t", "b", "1")
    req_a = ResourceRequest(
        key=manager.resource_key_for_path(alpha, mode=AccessMode.WRITE),
        mode=AccessMode.WRITE,
        path=manager.canonicalize_path(alpha),
    )
    req_b = ResourceRequest(
        key=manager.resource_key_for_path(beta, mode=AccessMode.WRITE),
        mode=AccessMode.WRITE,
        path=manager.canonicalize_path(beta),
    )
    manager.acquire_all((req_a,), owner_a)
    manager.acquire_all((req_b,), owner_b, timeout=0.05)


def test_sibling_paths_do_not_conflict(tmp_path: Path) -> None:
    manager = ResourceLeaseManager(default_ttl_s=2.0)
    left = tmp_path / "left.txt"
    right = tmp_path / "right.txt"
    left.write_text("a", encoding="utf-8")
    right.write_text("b", encoding="utf-8")
    owner_a = ResourceOwner("t", "a", "1")
    owner_b = ResourceOwner("t", "b", "1")
    req_a = ResourceRequest(
        key=manager.resource_key_for_path(left, mode=AccessMode.WRITE),
        mode=AccessMode.WRITE,
        path=manager.canonicalize_path(left),
    )
    req_b = ResourceRequest(
        key=manager.resource_key_for_path(right, mode=AccessMode.WRITE),
        mode=AccessMode.WRITE,
        path=manager.canonicalize_path(right),
    )
    manager.acquire_all((req_a,), owner_a)
    manager.acquire_all((req_b,), owner_b, timeout=0.05)


def test_read_shared_write_exclusive_and_idempotent_release(tmp_path: Path) -> None:
    manager = ResourceLeaseManager(default_ttl_s=2.0)
    target = tmp_path / "shared.txt"
    target.write_text("x", encoding="utf-8")
    read_a = ResourceOwner("t", "ra", "1")
    read_b = ResourceOwner("t", "rb", "1")
    write_a = ResourceOwner("t", "wa", "1")
    read_req = ResourceRequest(
        key=manager.resource_key_for_path(target, mode=AccessMode.READ),
        mode=AccessMode.READ,
        path=manager.canonicalize_path(target),
    )
    write_req = ResourceRequest(
        key=manager.resource_key_for_path(target, mode=AccessMode.WRITE),
        mode=AccessMode.WRITE,
        path=manager.canonicalize_path(target),
    )
    manager.acquire_all((read_req,), read_a)
    manager.acquire_all((read_req,), read_b)
    with pytest.raises(LeaseAcquisitionTimeout):
        manager.acquire_all((write_req,), write_a, timeout=0.02)
    manager.release_all((read_req,), read_a)
    manager.release_all((read_req,), read_a)
    manager.release_all((read_req,), read_b)


def test_reader_ttl_is_per_owner(tmp_path: Path) -> None:
    manager = ResourceLeaseManager(default_ttl_s=0.1)
    target = tmp_path / "shared.txt"
    target.write_text("x", encoding="utf-8")
    read_a = ResourceOwner("t", "ra", "1")
    read_b = ResourceOwner("t", "rb", "1")
    write_c = ResourceOwner("t", "wc", "1")
    read_req = ResourceRequest(
        key=manager.resource_key_for_path(target, mode=AccessMode.READ),
        mode=AccessMode.READ,
        path=manager.canonicalize_path(target),
    )
    write_req = ResourceRequest(
        key=manager.resource_key_for_path(target, mode=AccessMode.WRITE),
        mode=AccessMode.WRITE,
        path=manager.canonicalize_path(target),
    )
    manager.acquire_all((read_req,), read_b, ttl_s=0.1)
    manager.acquire_all((read_req,), read_a, ttl_s=0.1)
    time.sleep(0.05)
    manager.heartbeat((read_req,), read_a, ttl_s=2.0)
    time.sleep(0.1)
    with pytest.raises(LeaseAcquisitionTimeout):
        manager.acquire_all((write_req,), write_c, timeout=0.02)
    manager.release_all((read_req,), read_a)
    manager.acquire_all((write_req,), write_c, timeout=0.05)


def test_heartbeat_validates_entire_bundle(tmp_path: Path) -> None:
    manager = ResourceLeaseManager(default_ttl_s=2.0)
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    first.write_text("a", encoding="utf-8")
    second.write_text("b", encoding="utf-8")
    owner = ResourceOwner("t", "s", "1")
    req_a = ResourceRequest(
        key=manager.resource_key_for_path(first, mode=AccessMode.WRITE),
        mode=AccessMode.WRITE,
        path=manager.canonicalize_path(first),
    )
    req_b = ResourceRequest(
        key=manager.resource_key_for_path(second, mode=AccessMode.WRITE),
        mode=AccessMode.WRITE,
        path=manager.canonicalize_path(second),
    )
    manager.acquire_all((req_a,), owner)
    with pytest.raises(LeaseOwnershipError):
        manager.heartbeat((req_a, req_b), owner, ttl_s=0.2)


def test_release_all_validates_foreign_ownership_atomically(tmp_path: Path) -> None:
    manager = ResourceLeaseManager(default_ttl_s=2.0)
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    first.write_text("a", encoding="utf-8")
    second.write_text("b", encoding="utf-8")
    owner = ResourceOwner("t", "s", "1")
    foreign = ResourceOwner("t", "other", "1")
    req_a = ResourceRequest(
        key=manager.resource_key_for_path(first, mode=AccessMode.WRITE),
        mode=AccessMode.WRITE,
        path=manager.canonicalize_path(first),
    )
    req_b = ResourceRequest(
        key=manager.resource_key_for_path(second, mode=AccessMode.WRITE),
        mode=AccessMode.WRITE,
        path=manager.canonicalize_path(second),
    )
    manager.acquire_all((req_a, req_b), owner)
    with pytest.raises(LeaseOwnershipError):
        manager.release_all((req_a, req_b), foreign)
    manager.release_all((req_a, req_b), owner)
    manager.release_all((req_a, req_b), owner)


def test_global_resource_is_workspace_singleton() -> None:
    manager = ResourceLeaseManager()
    assert manager.resource_key_for_global() == WORKSPACE_GLOBAL_KEY
    assert manager.resource_key_for_global() == manager.resource_key_for_global("workspace")


def test_acquire_none_timeout_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import athena.lease.resource as resource_module

    monkeypatch.setattr(resource_module, "DEFAULT_ACQUIRE_TIMEOUT_S", 0.15)
    manager = ResourceLeaseManager(default_ttl_s=2.0)
    target = tmp_path / "held.txt"
    target.write_text("x", encoding="utf-8")
    owner_a = ResourceOwner("t", "a", "1")
    owner_b = ResourceOwner("t", "b", "1")
    req = ResourceRequest(
        key=manager.resource_key_for_path(target, mode=AccessMode.WRITE),
        mode=AccessMode.WRITE,
        path=manager.canonicalize_path(target),
    )
    manager.acquire_all((req,), owner_a, ttl_s=300.0)
    start = time.monotonic()
    with pytest.raises(LeaseAcquisitionTimeout):
        manager.acquire_all((req,), owner_b, timeout=None)
    elapsed = time.monotonic() - start
    assert 0.1 <= elapsed <= 1.0


# ---------------------------------------------------------------------------
# Executors for E2E
# ---------------------------------------------------------------------------


class _SharedFileSubprocessExecutor(SynchronousCancellableExecutor):
    def __init__(self, log_path: Path, target_name: str = "shared.txt") -> None:
        self._log = str(log_path)
        self._target = target_name

    def execute(self, *, subtask, workspace_root, attempt_id):
        try:
            root_repr = repr(str(Path(workspace_root)))
            target = self._target
            script = (
                "import sys,time; from pathlib import Path;"
                f"log=Path({self._log!r}); subtask=sys.argv[1]; root=Path({root_repr});"
                f"target={target!r};"
                "start=time.time();"
                "log.open('a', encoding='utf-8').write(f'{start}|start|{subtask}\\n');"
                "time.sleep(0.15); end=time.time();"
                "log.open('a', encoding='utf-8').write(f'{end}|end|{subtask}\\n');"
                "(root/target).write_text(subtask);"
            )
            proc = subprocess.run(
                [sys.executable, "-c", script, subtask.subtask_id],
                cwd=workspace_root,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            return proc.returncode, (target,)
        finally:
            self._mark_finished(attempt_id)


class _SubprocessOverlapExecutor(SynchronousCancellableExecutor):
    def __init__(self, log_path: Path) -> None:
        self._log = str(log_path)

    def execute(self, *, subtask, workspace_root, attempt_id):
        try:
            root_repr = repr(str(Path(workspace_root)))
            script = (
                "import sys,time; from pathlib import Path;"
                f"log=Path({self._log!r}); subtask=sys.argv[1]; root=Path({root_repr});"
                "start=time.time();"
                "log.open('a', encoding='utf-8').write(f'{start}|start|{subtask}\\n');"
                "time.sleep(0.15); end=time.time();"
                "log.open('a', encoding='utf-8').write(f'{end}|end|{subtask}\\n');"
                "out=root/f'{subtask}.txt'; out.write_text(subtask);"
            )
            proc = subprocess.run(
                [sys.executable, "-c", script, subtask.subtask_id],
                cwd=workspace_root,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr or proc.stdout or "subprocess failed")
            return proc.returncode, (f"{subtask.subtask_id}.txt",)
        finally:
            self._mark_finished(attempt_id)


class _SlowInterruptibleExecutor(InterruptibleSubprocessExecutor):
    def execute(self, *, subtask, workspace_root, attempt_id):
        script = "import time; time.sleep(2.0)"
        with self._lock:
            proc = subprocess.Popen(
                [sys.executable, "-c", script],
                cwd=workspace_root,
            )
            self._active[attempt_id] = proc
        try:
            return proc.wait(timeout=5), ()
        finally:
            with self._lock:
                self._active.pop(attempt_id, None)


class _OrderLoggingExecutor(SynchronousCancellableExecutor):
    def __init__(self, log_path: Path) -> None:
        self._log = log_path

    def execute(self, *, subtask, workspace_root, attempt_id):
        try:
            with self._log.open("a", encoding="utf-8") as handle:
                handle.write(f"{subtask.subtask_id}\n")
            target = Path(workspace_root) / f"{subtask.subtask_id}.txt"
            target.write_text("ok", encoding="utf-8")
            return 0, (str(target.name),)
        finally:
            self._mark_finished(attempt_id)


class _FailExecutor(SynchronousCancellableExecutor):
    def __init__(self, fail_id: str) -> None:
        self._fail_id = fail_id

    def execute(self, *, subtask, workspace_root, attempt_id):
        try:
            if subtask.subtask_id == self._fail_id:
                return 1, ()
            target = Path(workspace_root) / f"{subtask.subtask_id}.txt"
            target.write_text("ok", encoding="utf-8")
            return 0, (str(target.name),)
        finally:
            self._mark_finished(attempt_id)


class _OutOfScopeExecutor(SynchronousCancellableExecutor):
    def execute(self, *, subtask, workspace_root, attempt_id):
        try:
            allowed = Path(workspace_root) / "allowed.txt"
            allowed.write_text("ok", encoding="utf-8")
            offender = Path(workspace_root) / "offender.txt"
            offender.write_text("bad", encoding="utf-8")
            return 0, ("allowed.txt",)
        finally:
            self._mark_finished(attempt_id)


class _LyingExecutor(SynchronousCancellableExecutor):
    def execute(self, *, subtask, workspace_root, attempt_id):
        try:
            allowed = Path(workspace_root) / "allowed.txt"
            allowed.write_text("ok", encoding="utf-8")
            secret = Path(workspace_root) / "secret.txt"
            secret.write_text("hidden", encoding="utf-8")
            return 0, ("allowed.txt",)
        finally:
            self._mark_finished(attempt_id)


class _RaiseExecutor(SynchronousCancellableExecutor):
    def __init__(self, fail_id: str = "a") -> None:
        self._fail_id = fail_id

    def execute(self, *, subtask, workspace_root, attempt_id):
        try:
            if subtask.subtask_id == self._fail_id:
                raise ValueError("executor exploded")
            target = Path(workspace_root) / f"{subtask.subtask_id}.txt"
            target.write_text("ok", encoding="utf-8")
            return 0, (f"{subtask.subtask_id}.txt",)
        finally:
            self._mark_finished(attempt_id)


class _NonCancellableExecutor:
    def execute(self, *, subtask, workspace_root, attempt_id):
        return 0, ()


# ---------------------------------------------------------------------------
# Harmonia E2E
# ---------------------------------------------------------------------------


def test_independent_subtasks_overlap_in_subprocess(tmp_path: Path) -> None:
    repo = tmp_path / "synthetic-repo"
    authority = SyntheticGitWorktreeAuthority.bootstrap(repo)
    log_path = tmp_path.parent / f"{repo.name}-overlap.log"
    engine = _engine(
        repo,
        _SubprocessOverlapExecutor(log_path),
        worktree_authority=authority,
        max_workers=2,
    )
    plan = _plan(
        _subtask("a", write_scope=("a.txt",), workspace_root=str(repo), parallel=True),
        _subtask("b", write_scope=("b.txt",), workspace_root=str(repo), parallel=True),
        max_parallelism=2,
        workspace_root=str(repo),
        parallel=True,
    )
    result = engine.run(plan)
    assert all(outcome.state == SubtaskState.COMPLETED for outcome in result.outcomes)
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    intervals: dict[str, tuple[float, float]] = {}
    for line in lines:
        timestamp_text, kind, subtask_id = line.split("|")
        timestamp = float(timestamp_text)
        if kind == "start":
            intervals.setdefault(subtask_id, (timestamp, timestamp))
        else:
            start, _ = intervals[subtask_id]
            intervals[subtask_id] = (start, timestamp)
    a_start, a_end = intervals["a"]
    b_start, b_end = intervals["b"]
    assert a_start < b_end and b_start < a_end


def test_same_file_tasks_serialize(tmp_path: Path) -> None:
    log_path = tmp_path.parent / f"{tmp_path.name}-serial.log"
    engine = _engine(
        tmp_path,
        _SharedFileSubprocessExecutor(log_path),
        max_workers=2,
    )
    plan = _plan(
        _subtask("a", write_scope=("shared.txt",), workspace_root=str(tmp_path)),
        _subtask("b", write_scope=("shared.txt",), workspace_root=str(tmp_path)),
        max_parallelism=2,
        workspace_root=str(tmp_path),
    )
    result = engine.run(plan)
    assert all(outcome.state == SubtaskState.COMPLETED for outcome in result.outcomes)
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    intervals: dict[str, tuple[float, float]] = {}
    for line in lines:
        timestamp_text, kind, subtask_id = line.split("|")
        timestamp = float(timestamp_text)
        if kind == "start":
            intervals.setdefault(subtask_id, (timestamp, timestamp))
        else:
            start, _ = intervals[subtask_id]
            intervals[subtask_id] = (start, timestamp)
    a_start, a_end = intervals["a"]
    b_start, b_end = intervals["b"]
    assert a_end <= b_start or b_end <= a_start


def test_dependency_order_and_dependent_only_cancellation(tmp_path: Path) -> None:
    repo = tmp_path / "order-repo"
    authority = SyntheticGitWorktreeAuthority.bootstrap(repo)
    log_path = tmp_path.parent / f"{repo.name}-order.log"
    engine = _engine(
        repo,
        _OrderLoggingExecutor(log_path),
        worktree_authority=authority,
    )
    plan = _plan(
        _subtask("a", workspace_root=str(repo), parallel=True),
        _subtask("b", dependencies=("a",), workspace_root=str(repo)),
        _subtask("c", dependencies=("b",), workspace_root=str(repo)),
        _subtask("d", workspace_root=str(repo), parallel=True),
        workspace_root=str(repo),
        parallel=True,
    )
    result = engine.run(plan)
    assert [item.subtask_id for item in result.outcomes if item.state == SubtaskState.COMPLETED]
    order = log_path.read_text(encoding="utf-8").split()
    assert order.index("a") < order.index("b") < order.index("c")

    fail_root = tmp_path / "fail-repo"
    fail_authority = SyntheticGitWorktreeAuthority.bootstrap(fail_root)
    fail_engine = _engine(
        fail_root,
        _FailExecutor("a"),
        worktree_authority=fail_authority,
    )
    fail_plan = _plan(
        _subtask("a", workspace_root=str(fail_root), parallel=True),
        _subtask("b", dependencies=("a",), workspace_root=str(fail_root)),
        _subtask("d", workspace_root=str(fail_root), parallel=True),
        workspace_root=str(fail_root),
        parallel=True,
    )
    fail_result = fail_engine.run(fail_plan)
    states = {item.subtask_id: item.state for item in fail_result.outcomes}
    assert states["a"] == SubtaskState.FAILED
    assert states["b"] == SubtaskState.CANCELLED
    assert REASON_SUBTASK_CANCELLED in next(
        item.reason_codes for item in fail_result.outcomes if item.subtask_id == "b"
    )
    assert states["d"] == SubtaskState.COMPLETED


def test_out_of_scope_offender_isolated(tmp_path: Path) -> None:
    engine = _engine(tmp_path, _OutOfScopeExecutor())
    plan = _plan(
        _subtask("a", write_scope=("allowed.txt",), workspace_root=str(tmp_path)),
        workspace_root=str(tmp_path),
    )
    result = engine.run(plan)
    offender = tmp_path / "offender.txt"
    assert offender.exists()
    outcome = result.outcomes[0]
    assert outcome.state == SubtaskState.FAILED
    assert REASON_OUT_OF_SCOPE in outcome.reason_codes


def test_lying_executor_caught_by_filesystem_verify(tmp_path: Path) -> None:
    engine = _engine(tmp_path, _LyingExecutor())
    plan = _plan(
        _subtask("a", write_scope=("allowed.txt",), workspace_root=str(tmp_path)),
        workspace_root=str(tmp_path),
    )
    result = engine.run(plan)
    assert result.outcomes[0].state == SubtaskState.FAILED
    assert REASON_OUT_OF_SCOPE in result.outcomes[0].reason_codes


def test_forged_seal_rejected(tmp_path: Path) -> None:
    engine = _engine(tmp_path, _OrderLoggingExecutor(tmp_path / "log.txt"))
    bad = SubtaskSpec(
        subtask_id="a",
        dependencies=(),
        worker_id="worker-a",
        read_scope=(),
        write_scope=("a.txt",),
        operation_type="file_edit",
        resources=_BUDGET,
        seal_hash="f" * 64,
    )
    plan = TeamPlan(
        task_id="task-001",
        subtasks=(bad,),
        max_parallelism=1,
        project_parallelism=1,
        aegis_parallelism=1,
    )
    result = engine.run(plan)
    assert result.outcomes[0].state == SubtaskState.FAILED
    assert REASON_UNAUTHORIZED in result.outcomes[0].reason_codes


def test_subtask_timeout_stops_execution(tmp_path: Path) -> None:
    engine = _engine(tmp_path, _SlowInterruptibleExecutor(), max_workers=1)
    plan = _plan(
        _subtask("a", workspace_root=str(tmp_path), deadline_s=0.2),
        workspace_root=str(tmp_path),
    )
    start = time.monotonic()
    result = engine.run(plan)
    elapsed = time.monotonic() - start
    assert elapsed < 1.5
    assert result.outcomes[0].state == SubtaskState.FAILED
    assert REASON_SUBTASK_TIMEOUT in result.outcomes[0].reason_codes


def test_engine_run_admission_busy(tmp_path: Path) -> None:
    engine = _engine(
        tmp_path,
        _SlowInterruptibleExecutor(),
        max_concurrent_runs=1,
        max_workers=1,
    )
    plan = _plan(
        _subtask("a", workspace_root=str(tmp_path), deadline_s=1.0),
        workspace_root=str(tmp_path),
    )
    gate = threading.Event()
    results: list = []

    def first_run() -> None:
        results.append(engine.run(plan))
        gate.set()

    thread = threading.Thread(target=first_run)
    thread.start()
    time.sleep(0.05)
    busy = engine.run(plan)
    assert busy.reason_codes == (REASON_BUSY,)
    assert all(item.state == SubtaskState.FAILED for item in busy.outcomes)
    assert gate.wait(timeout=3.0)
    thread.join(timeout=3.0)


def test_token_double_release_cannot_inflate_capacity(tmp_path: Path) -> None:
    from athena.harmonia.resources import ResourceTokenPool, TokenPoolLimits

    pool = ResourceTokenPool(TokenPoolLimits(cpu_tokens=1, ram_mb=64, gpu_tokens=0, provider_tokens=1))
    reservation = pool.try_acquire(_BUDGET)
    assert reservation is not None
    pool.release(reservation)
    pool.release(reservation)
    second = pool.try_acquire(_BUDGET)
    assert second is not None
    third = pool.try_acquire(_BUDGET)
    assert third is None
    pool.release(second)


def test_worktree_opaque_name_rejects_adversarial(tmp_path: Path) -> None:
    repo = tmp_path / "synthetic-repo"
    authority = SyntheticGitWorktreeAuthority.bootstrap(repo)
    for bad_name in ("../escape", "a/b", ".hidden", "/abs", ".." ):
        with pytest.raises(HarmoniaError):
            authority.create_worktree(
                repository_root=str(repo),
                base_ref="HEAD",
                opaque_name=bad_name,
            )


def test_worktree_cleanup_failure_surfaces(tmp_path: Path) -> None:
    repo = tmp_path / "synthetic-repo"
    authority = SyntheticGitWorktreeAuthority.bootstrap(repo)

    class _TouchExecutor(SynchronousCancellableExecutor):
        def execute(self, *, subtask, workspace_root, attempt_id):
            try:
                Path(workspace_root, "worktree.txt").write_text("ok", encoding="utf-8")
                return 0, ("worktree.txt",)
            finally:
                self._mark_finished(attempt_id)

    engine = _engine(
        repo,
        _TouchExecutor(),
        worktree_authority=authority,
    )
    plan = _plan(
        _subtask("a", operation_type="git", write_scope=(), workspace_root=str(repo)),
        workspace_root=str(repo),
    )
    with patch.object(authority, "remove_worktree", side_effect=HarmoniaError(REASON_WORKTREE_CLEANUP_FAILED)):
        result = engine.run(plan)
    assert result.outcomes[0].state == SubtaskState.FAILED
    assert REASON_WORKTREE_CLEANUP_FAILED in result.outcomes[0].reason_codes


def test_worktree_denied_without_authority(tmp_path: Path) -> None:
    engine = _engine(
        tmp_path,
        _OrderLoggingExecutor(tmp_path / "log.txt"),
        worktree_authority=DenyWorktreeAuthority(),
    )
    plan = _plan(
        _subtask("a", write_scope=tuple(f"path{i}.txt" for i in range(20)), workspace_root=str(tmp_path)),
        workspace_root=str(tmp_path),
    )
    result = engine.run(plan)
    assert result.outcomes[0].state == SubtaskState.FAILED
    assert REASON_WORKTREE_DENIED in result.outcomes[0].reason_codes


def test_synthetic_git_worktree_e2e(tmp_path: Path) -> None:
    repo = tmp_path / "synthetic-repo"
    authority = SyntheticGitWorktreeAuthority.bootstrap(repo)

    class _WorktreeTouchExecutor(SynchronousCancellableExecutor):
        def execute(self, *, subtask, workspace_root, attempt_id):
            try:
                target = Path(workspace_root) / "worktree.txt"
                target.write_text("ok", encoding="utf-8")
                return 0, ("worktree.txt",)
            finally:
                self._mark_finished(attempt_id)

    engine = _engine(
        repo,
        _WorktreeTouchExecutor(),
        worktree_authority=authority,
    )
    plan = _plan(
        _subtask("a", operation_type="git", write_scope=(), workspace_root=str(repo)),
        workspace_root=str(repo),
    )
    result = engine.run(plan)
    assert result.outcomes[0].state == SubtaskState.COMPLETED
    assert result.outcomes[0].isolation == IsolationStrategy.WORKTREE
    assert not any(repo.glob(".harmonia_worktrees/*"))


def test_token_busy_returns_sanitized_reason(tmp_path: Path) -> None:
    from athena.harmonia.resources import ResourceTokenPool, TokenPoolLimits

    pool = ResourceTokenPool(
        TokenPoolLimits(cpu_tokens=0, ram_mb=0, gpu_tokens=0, provider_tokens=0)
    )
    engine = _engine(
        tmp_path,
        _OrderLoggingExecutor(tmp_path / "log.txt"),
        token_pool=pool,
    )
    result = engine.run(
        _plan(_subtask("a", workspace_root=str(tmp_path)), workspace_root=str(tmp_path))
    )
    assert result.outcomes[0].state == SubtaskState.FAILED
    assert REASON_BUSY in result.outcomes[0].reason_codes


def test_seven_mcp_tools_preserved() -> None:
    assert TOOL_NAMES == (
        "run_combo",
        "ask_provider",
        "get_execution",
        "list_executions",
        "cancel_execution",
        "submit_task",
        "get_task",
    )


def test_harmonia_imports_no_operational_core() -> None:
    package = Path(__file__).resolve().parents[1] / "athena" / "harmonia"
    forbidden = {
        "athena.mcp_server",
        "athena.router",
        "athena.bridge",
        "athena.execution",
        "athena.flow",
        "athena.tasks",
        "aegis",
    }
    imported: set[str] = set()
    for module in package.glob("*.py"):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
    assert not {
        name
        for name in imported
        if name in forbidden or name.startswith(tuple(f + "." for f in forbidden))
    }


def test_benchmark_harmonia_guardrail() -> None:
    report = harmonia_benchmark.build_report(
        harmonia_benchmark.BenchmarkConfig(
            samples=5,
            warmups=2,
            guardrail=True,
            plan_ceiling_ms=50.0,
            reserve_ceiling_ms=50.0,
        )
    )
    assert report["guardrail_pass"] is True


def test_global_lease_conflicts_with_path_read_write(tmp_path: Path) -> None:
    manager = ResourceLeaseManager(default_ttl_s=2.0)
    target = tmp_path / "file.txt"
    target.write_text("x", encoding="utf-8")
    global_owner = ResourceOwner("t", "global", "1")
    path_owner = ResourceOwner("t", "path", "1")
    global_req = ResourceRequest(
        key=manager.resource_key_for_global(),
        mode=AccessMode.GLOBAL,
        path=None,
    )
    write_req = ResourceRequest(
        key=manager.resource_key_for_path(target, mode=AccessMode.WRITE),
        mode=AccessMode.WRITE,
        path=manager.canonicalize_path(target),
    )
    read_req = ResourceRequest(
        key=manager.resource_key_for_path(target, mode=AccessMode.READ),
        mode=AccessMode.READ,
        path=manager.canonicalize_path(target),
    )
    manager.acquire_all((global_req,), global_owner)
    with pytest.raises(LeaseAcquisitionTimeout):
        manager.acquire_all((write_req,), path_owner, timeout=0.02)
    with pytest.raises(LeaseAcquisitionTimeout):
        manager.acquire_all((read_req,), path_owner, timeout=0.02)
    manager.release_all((global_req,), global_owner)
    manager.acquire_all((write_req,), path_owner)
    with pytest.raises(LeaseAcquisitionTimeout):
        manager.acquire_all((global_req,), global_owner, timeout=0.02)


def test_resource_request_validates_key_and_duplicates(tmp_path: Path) -> None:
    manager = ResourceLeaseManager(default_ttl_s=2.0)
    target = tmp_path / "a.txt"
    target.write_text("a", encoding="utf-8")
    canonical = manager.canonicalize_path(target)
    owner = ResourceOwner("t", "s", "1")
    good = ResourceRequest(
        key=manager.resource_key_for_path(canonical, mode=AccessMode.WRITE),
        mode=AccessMode.WRITE,
        path=canonical,
    )
    manager.acquire_all((good,), owner)
    manager.release_all((good,), owner)
    bad_key = ResourceRequest(
        key="file:/wrong",
        mode=AccessMode.WRITE,
        path=canonical,
    )
    with pytest.raises(ValueError, match="does not match"):
        manager.acquire_all((bad_key,), owner, timeout=0.02)
    duplicate = (
        good,
        ResourceRequest(
            key=manager.resource_key_for_path(canonical, mode=AccessMode.WRITE),
            mode=AccessMode.WRITE,
            path=canonical,
        ),
    )
    with pytest.raises(ValueError, match="duplicate"):
        manager.acquire_all(duplicate, owner, timeout=0.02)


def test_budget_bool_coercion_rejected() -> None:
    with pytest.raises(TypeError):
        ResourceBudget(True, 64, 0, 1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=REASON_INVALID_PLAN):
        parse_team_plan(
            {
                "task_id": "t",
                "max_parallelism": 1,
                "project_parallelism": 1,
                "aegis_parallelism": 1,
                "subtasks": [
                    {
                        "subtask_id": "a",
                        "dependencies": [],
                        "worker_id": "w",
                        "read_scope": [],
                        "write_scope": ["a.txt"],
                        "operation_type": "file_edit",
                        "resources": {
                            "cpu_tokens": True,
                            "ram_mb": 64,
                            "gpu_tokens": 0,
                            "provider_tokens": 1,
                        },
                        "seal_hash": "a" * 64,
                    }
                ],
            }
        )


def test_oversized_subtask_rejected_at_plan(tmp_path: Path) -> None:
    from athena.harmonia.resources import TokenPoolLimits

    huge = _subtask("a", workspace_root=str(tmp_path))
    plan = _plan(huge, workspace_root=str(tmp_path))
    with pytest.raises(HarmoniaError) as caught:
        build_execution_plan(
            plan,
            workspace_root=str(tmp_path),
            max_workers=4,
            token_limits=TokenPoolLimits(cpu_tokens=0, ram_mb=0, gpu_tokens=0, provider_tokens=0),
        )
    assert REASON_BUSY in caught.value.reason_codes


def test_executor_exception_becomes_offender_outcome(tmp_path: Path) -> None:
    repo = tmp_path / "synthetic-repo"
    authority = SyntheticGitWorktreeAuthority.bootstrap(repo)
    engine = _engine(
        repo,
        _RaiseExecutor(),
        worktree_authority=authority,
        max_workers=2,
    )
    plan = _plan(
        _subtask("a", workspace_root=str(repo), parallel=True),
        _subtask("b", workspace_root=str(repo), parallel=True),
        max_parallelism=2,
        workspace_root=str(repo),
        parallel=True,
    )
    result = engine.run(plan)
    states = {item.subtask_id: item.state for item in result.outcomes}
    assert states["a"] == SubtaskState.FAILED
    assert states["b"] == SubtaskState.COMPLETED
    assert REASON_SUBTASK_FAILED in next(
        o.reason_codes for o in result.outcomes if o.subtask_id == "a"
    )


def test_non_cancellable_executor_rejected(tmp_path: Path) -> None:
    engine = _engine(tmp_path, _NonCancellableExecutor())
    result = engine.run(_plan(_subtask("a", workspace_root=str(tmp_path)), workspace_root=str(tmp_path)))
    assert result.outcomes[0].state == SubtaskState.FAILED
    assert REASON_EXECUTOR_NON_CANCELLABLE in result.outcomes[0].reason_codes


def test_same_size_mtime_restore_still_detected(tmp_path: Path) -> None:
    (tmp_path / "allowed.txt").write_text("ok", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("baseline!", encoding="utf-8")

    class _MtimeCheatExecutor(SynchronousCancellableExecutor):
        def execute(self, *, subtask, workspace_root, attempt_id):
            try:
                secret = Path(workspace_root) / "secret.txt"
                original_mtime = secret.stat().st_mtime_ns
                secret.write_text("injectd!!", encoding="utf-8")
                os.utime(secret, ns=(original_mtime, original_mtime))
                allowed = Path(workspace_root) / "allowed.txt"
                allowed.write_text("ok", encoding="utf-8")
                return 0, ("allowed.txt",)
            finally:
                self._mark_finished(attempt_id)

    engine = _engine(tmp_path, _MtimeCheatExecutor())
    result = engine.run(
        _plan(
            _subtask("a", write_scope=("allowed.txt",), workspace_root=str(tmp_path)),
            workspace_root=str(tmp_path),
        )
    )
    assert result.outcomes[0].state == SubtaskState.FAILED
    assert REASON_OUT_OF_SCOPE in result.outcomes[0].reason_codes


def test_unrelated_directory_mutation_detected(tmp_path: Path) -> None:
    class _SiblingExecutor(SynchronousCancellableExecutor):
        def execute(self, *, subtask, workspace_root, attempt_id):
            try:
                allowed = Path(workspace_root) / "allowed.txt"
                allowed.write_text("ok", encoding="utf-8")
                (Path(workspace_root) / "other").mkdir(exist_ok=True)
                (Path(workspace_root) / "other" / "secret.txt").write_text("x", encoding="utf-8")
                return 0, ("allowed.txt",)
            finally:
                self._mark_finished(attempt_id)

    engine = _engine(tmp_path, _SiblingExecutor())
    result = engine.run(
        _plan(
            _subtask("a", write_scope=("allowed.txt",), workspace_root=str(tmp_path)),
            workspace_root=str(tmp_path),
        )
    )
    assert result.outcomes[0].state == SubtaskState.FAILED
    assert REASON_OUT_OF_SCOPE in result.outcomes[0].reason_codes


def test_parallel_worktree_infra_not_out_of_scope(tmp_path: Path) -> None:
    from athena.harmonia.verify import (
        filter_infrastructure_changes,
        is_infrastructure_path,
    )

    assert is_infrastructure_path(".git")
    assert is_infrastructure_path(".harmonia_worktrees")
    assert is_infrastructure_path(".harmonia_worktrees/wtabc")
    assert not is_infrastructure_path("a.txt")
    assert filter_infrastructure_changes((".git", ".harmonia_worktrees", "a.txt")) == ("a.txt",)

    repo = tmp_path / "infra-repo"
    authority = SyntheticGitWorktreeAuthority.bootstrap(repo)
    engine = _engine(
        repo,
        _OrderLoggingExecutor(tmp_path / "infra.log"),
        worktree_authority=authority,
        max_workers=2,
    )
    plan = _plan(
        _subtask("a", workspace_root=str(repo), parallel=True),
        _subtask("b", workspace_root=str(repo), parallel=True),
        max_parallelism=2,
        workspace_root=str(repo),
        parallel=True,
    )
    result = engine.run(plan)
    assert all(outcome.state == SubtaskState.COMPLETED for outcome in result.outcomes)


def test_worktree_evidence_digest_before_cleanup(tmp_path: Path) -> None:
    repo = tmp_path / "synthetic-repo"
    authority = SyntheticGitWorktreeAuthority.bootstrap(repo)

    class _WorktreeTouchExecutor(SynchronousCancellableExecutor):
        def execute(self, *, subtask, workspace_root, attempt_id):
            try:
                target = Path(workspace_root) / "worktree.txt"
                target.write_text("evidence", encoding="utf-8")
                return 0, ("worktree.txt",)
            finally:
                self._mark_finished(attempt_id)

    engine = _engine(repo, _WorktreeTouchExecutor(), worktree_authority=authority)
    result = engine.run(
        _plan(
            _subtask("a", operation_type="git", write_scope=(), workspace_root=str(repo)),
            workspace_root=str(repo),
        )
    )
    outcome = result.outcomes[0]
    assert outcome.state == SubtaskState.COMPLETED
    assert outcome.evidence_digest is not None
    assert len(outcome.evidence_digest) == 64
    assert "worktree.txt" in outcome.evidence_paths


def test_subtask_cancellation_prevents_post_release_writes(tmp_path: Path) -> None:
    marker = tmp_path / "post_release.txt"

    class _SlowCancellableExecutor(InterruptibleSubprocessExecutor):
        def execute(self, *, subtask, workspace_root, attempt_id):
            marker_repr = repr(str(marker))
            script = (
                "import time; from pathlib import Path;"
                "time.sleep(1.0);"
                f"Path({marker_repr}).write_text('late');"
            )
            with self._lock:
                proc = subprocess.Popen(
                    [sys.executable, "-c", script],
                    cwd=workspace_root,
                )
                self._active[attempt_id] = proc
            try:
                return proc.wait(timeout=5), ()
            finally:
                with self._lock:
                    self._active.pop(attempt_id, None)

    engine = _engine(tmp_path, _SlowCancellableExecutor(), max_workers=1)
    plan = _plan(
        _subtask("a", workspace_root=str(tmp_path), deadline_s=2.0),
        workspace_root=str(tmp_path),
    )
    result_holder: list = []

    def run_plan() -> None:
        result_holder.append(engine.run(plan))

    thread = threading.Thread(target=run_plan)
    thread.start()
    time.sleep(0.05)
    engine.request_cancel("task-001", "a")
    thread.join(timeout=5.0)
    assert result_holder
    outcome = result_holder[0].outcomes[0]
    assert outcome.state in (SubtaskState.CANCELLED, SubtaskState.FAILED)
    time.sleep(0.2)
    assert not marker.exists()


def test_engine_shutdown_leaves_no_residue(tmp_path: Path) -> None:
    before = {t.ident for t in threading.enumerate()}
    with _engine(tmp_path, _OrderLoggingExecutor(tmp_path / "log.txt")) as engine:
        engine.run(_plan(_subtask("a", workspace_root=str(tmp_path)), workspace_root=str(tmp_path)))
    lingering = [
        t
        for t in threading.enumerate()
        if t.ident not in before and t.name.startswith("harmonia-")
    ]
    assert not lingering or all(not thread.is_alive() for thread in lingering)


def test_global_operation_serializes(tmp_path: Path) -> None:
    repo = tmp_path / "synthetic-repo"
    authority = SyntheticGitWorktreeAuthority.bootstrap(repo)
    log_path = tmp_path.parent / f"{repo.name}-global.log"
    engine = _engine(
        repo,
        _SubprocessOverlapExecutor(log_path),
        worktree_authority=authority,
        max_workers=2,
    )
    plan = _plan(
        _subtask("a", operation_type="git", write_scope=(), workspace_root=str(repo)),
        _subtask("b", operation_type="git", write_scope=(), workspace_root=str(repo)),
        max_parallelism=2,
        workspace_root=str(repo),
    )
    result = engine.run(plan)
    assert all(item.state == SubtaskState.COMPLETED for item in result.outcomes)
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    intervals: dict[str, tuple[float, float]] = {}
    for line in lines:
        timestamp_text, kind, subtask_id = line.split("|")
        timestamp = float(timestamp_text)
        if kind == "start":
            intervals.setdefault(subtask_id, (timestamp, timestamp))
        else:
            start, _ = intervals[subtask_id]
            intervals[subtask_id] = (start, timestamp)
    a_start, a_end = intervals["a"]
    b_start, b_end = intervals["b"]
    assert a_end <= b_start or b_end <= a_start
