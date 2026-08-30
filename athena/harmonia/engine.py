"""Motor Harmonia — planejamento, reserva, execução injetada e verificação."""

from __future__ import annotations

import os
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from athena.lease import (
    AccessMode,
    LeaseAcquisitionTimeout,
    LeaseOwnershipError,
    ResourceLeaseManager,
    ResourceOwner,
    ResourceRequest,
)
from athena.lease.resource import DEFAULT_TTL_S

from .authorization import DenyScopeEnforcementAuthority, DenySealAuthorizer
from .contracts import (
    CLEANUP_TIMEOUT_S,
    EVIDENCE_CAPTURE_TIMEOUT_S,
    GLOBAL_OPERATION_TYPES,
    MAX_QUEUE_DEPTH,
    MAX_WORKERS,
    REASON_BUSY,
    REASON_EVIDENCE_CAPTURE_FAILED,
    REASON_EXECUTOR_NON_CANCELLABLE,
    REASON_EXECUTOR_NON_TERMINATED,
    REASON_INVENTORY_EXCEEDED,
    REASON_OUT_OF_SCOPE,
    REASON_SUBTASK_CANCELLED,
    REASON_SUBTASK_FAILED,
    REASON_SUBTASK_TIMEOUT,
    REASON_UNAUTHORIZED,
    REASON_WORKTREE_CLEANUP_FAILED,
    SHUTDOWN_TIMEOUT_S,
    BusyResult,
    ExecutionPlan,
    HarmoniaError,
    IsolationStrategy,
    ScopeEnforcementAuthority,
    SealAuthorizer,
    SubtaskExecutor,
    SubtaskOutcome,
    SubtaskSpec,
    SubtaskState,
    TeamPlan,
    TeamRunResult,
    WorktreeAuthority,
    WorktreeDeniedError,
    validate_team_plan,
)
from .paths import canonicalize_scope
from .planner import build_execution_plan
from .resources import ResourceTokenPool, TokenPoolLimits, TokenReservation
from .strategy import choose_isolation
from .supervised import ExecutionResultKind, SupervisedExecutorAdapter
from .verify import (
    compute_evidence_digest,
    diff_snapshots,
    filter_infrastructure_changes,
    inventory_workspace,
    map_worktree_changes,
)
from .worktree import DenyWorktreeAuthority

LEASE_ACQUIRE_TIMEOUT_S = 0.25
HEARTBEAT_INTERVAL_DIVISOR = 3


@dataclass(frozen=True, slots=True)
class _ActiveReservation:
    requests: tuple[ResourceRequest, ...]
    owner: ResourceOwner
    token: TokenReservation | None
    token_released: bool
    attempt_id: str
    terminated: bool = False


@dataclass(slots=True)
class _RunningSubtask:
    attempt_id: str
    cancel_event: threading.Event
    future: Future[SubtaskOutcome] | None = None


class HarmoniaEngine:
    """Executor backend limitado para planos de equipe já autorizados."""

    def __init__(
        self,
        *,
        workspace_root: str,
        executor: SubtaskExecutor,
        lease_manager: ResourceLeaseManager | None = None,
        token_pool: ResourceTokenPool | None = None,
        worktree_authority: WorktreeAuthority | None = None,
        seal_authorizer: SealAuthorizer | None = None,
        scope_enforcement_authority: ScopeEnforcementAuthority | None = None,
        max_workers: int = MAX_WORKERS,
        max_concurrent_runs: int = MAX_QUEUE_DEPTH,
    ) -> None:
        self._workspace_root = os.path.realpath(workspace_root)
        self._executor = executor
        self._supervised = SupervisedExecutorAdapter(executor, max_workers=max_workers)
        self._lease_manager = lease_manager or ResourceLeaseManager()
        self._token_pool = token_pool or ResourceTokenPool(TokenPoolLimits())
        self._worktree_authority = worktree_authority or DenyWorktreeAuthority()
        self._seal_authorizer = seal_authorizer or DenySealAuthorizer()
        self._scope_enforcement = (
            scope_enforcement_authority or DenyScopeEnforcementAuthority()
        )
        if isinstance(max_workers, bool) or not isinstance(max_workers, int):
            raise TypeError("max_workers invalid")
        if not 1 <= max_workers <= MAX_WORKERS:
            raise ValueError("max_workers out of bounds")
        if isinstance(max_concurrent_runs, bool) or not isinstance(max_concurrent_runs, int):
            raise TypeError("max_concurrent_runs invalid")
        if not 1 <= max_concurrent_runs <= MAX_QUEUE_DEPTH:
            raise ValueError("max_concurrent_runs out of bounds")
        self._max_workers = max_workers
        self._max_concurrent_runs = max_concurrent_runs
        self._active_runs = 0
        self._run_lock = threading.Lock()
        self._running: dict[str, _RunningSubtask] = {}
        self._running_lock = threading.Lock()
        self._shutdown = threading.Event()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.shutdown()

    def shutdown(self, *, timeout_s: float = SHUTDOWN_TIMEOUT_S) -> None:
        self._shutdown.set()
        with self._running_lock:
            for state in self._running.values():
                state.cancel_event.set()
                if state.attempt_id:
                    self._supervised.cancel_attempt(state.attempt_id)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            with self._running_lock:
                if not self._running:
                    break
            time.sleep(0.01)
        self._supervised.shutdown(timeout_s=timeout_s)

    def plan(self, team_plan: TeamPlan) -> ExecutionPlan:
        validated = validate_team_plan(team_plan)
        return build_execution_plan(
            validated,
            workspace_root=self._workspace_root,
            max_workers=self._max_workers,
            token_limits=self._token_pool.limits,
        )

    def reserve_only(self, team_plan: TeamPlan) -> ExecutionPlan | BusyResult:
        with self._run_lock:
            if self._active_runs >= self._max_concurrent_runs:
                estimate = self._lease_manager.busy_estimate()
                return BusyResult(
                    reason_codes=(REASON_BUSY,),
                    waiters_ahead=estimate.waiters_ahead,
                    estimated_wait_s=estimate.estimated_wait_s,
                )
        return self.plan(team_plan)

    def request_cancel(self, task_id: str, *subtask_ids: str) -> None:
        prefix = f"{task_id}:"
        with self._running_lock:
            for key, state in self._running.items():
                if not key.startswith(prefix):
                    continue
                if subtask_ids and not any(key.endswith(f":{sid}") for sid in subtask_ids):
                    continue
                state.cancel_event.set()
                if state.attempt_id:
                    self._supervised.cancel_attempt(state.attempt_id)

    def run(self, team_plan: TeamPlan) -> TeamRunResult:
        try:
            validated = validate_team_plan(team_plan)
        except (TypeError, ValueError, HarmoniaError) as exc:
            codes = exc.reason_codes if isinstance(exc, HarmoniaError) else (str(exc),)
            return TeamRunResult(
                task_id=getattr(team_plan, "task_id", "unknown"),
                outcomes=tuple(
                    SubtaskOutcome(
                        subtask_id=subtask.subtask_id,
                        state=SubtaskState.FAILED,
                        reason_codes=codes[:1] if codes else (REASON_BUSY,),
                    )
                    for subtask in getattr(team_plan, "subtasks", ())
                ),
                reason_codes=codes[:1] if codes else (REASON_BUSY,),
            )

        with self._run_lock:
            if self._active_runs >= self._max_concurrent_runs:
                return TeamRunResult(
                    task_id=validated.task_id,
                    outcomes=tuple(
                        SubtaskOutcome(
                            subtask_id=subtask.subtask_id,
                            state=SubtaskState.FAILED,
                            reason_codes=(REASON_BUSY,),
                        )
                        for subtask in validated.subtasks
                    ),
                    reason_codes=(REASON_BUSY,),
                )
            self._active_runs += 1

        try:
            return self._run_inner(validated)
        finally:
            with self._run_lock:
                self._active_runs = max(0, self._active_runs - 1)

    def _run_inner(self, team_plan: TeamPlan) -> TeamRunResult:
        if not self._supervised.is_cancellable():
            return TeamRunResult(
                task_id=team_plan.task_id,
                outcomes=tuple(
                    SubtaskOutcome(
                        subtask_id=subtask.subtask_id,
                        state=SubtaskState.FAILED,
                        reason_codes=(REASON_EXECUTOR_NON_CANCELLABLE,),
                    )
                    for subtask in team_plan.subtasks
                ),
                reason_codes=(REASON_EXECUTOR_NON_CANCELLABLE,),
            )

        try:
            execution_plan = self.plan(team_plan)
        except HarmoniaError as exc:
            return TeamRunResult(
                task_id=team_plan.task_id,
                outcomes=tuple(
                    SubtaskOutcome(
                        subtask_id=subtask.subtask_id,
                        state=SubtaskState.FAILED,
                        reason_codes=exc.reason_codes,
                    )
                    for subtask in team_plan.subtasks
                ),
                reason_codes=exc.reason_codes,
            )

        by_id = {subtask.subtask_id: subtask for subtask in team_plan.subtasks}
        outcomes: dict[str, SubtaskOutcome] = {
            subtask.subtask_id: SubtaskOutcome(
                subtask_id=subtask.subtask_id,
                state=SubtaskState.PENDING,
                reason_codes=(),
            )
            for subtask in team_plan.subtasks
        }
        cancelled: set[str] = set()
        dependents = _build_dependents(team_plan.subtasks)

        def cancel_dependents(failed_id: str) -> None:
            stack = [failed_id]
            while stack:
                current = stack.pop()
                for child in dependents.get(current, ()):
                    if child in cancelled:
                        continue
                    cancelled.add(child)
                    self.request_cancel(team_plan.task_id, child)
                    outcomes[child] = SubtaskOutcome(
                        subtask_id=child,
                        state=SubtaskState.CANCELLED,
                        reason_codes=(REASON_SUBTASK_CANCELLED,),
                    )
                    stack.append(child)

        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            for group in execution_plan.groups:
                futures: list[tuple[str, Future[SubtaskOutcome]]] = []
                parallel_writers = len(group.subtask_ids) > 1
                parallel_authorized_by_id: dict[str, tuple[str, ...]] = {}
                if parallel_writers:
                    root = Path(self._workspace_root)
                    for peer_id in group.subtask_ids:
                        peer_paths: list[str] = []
                        for other_id in group.subtask_ids:
                            if other_id == peer_id:
                                continue
                            other = by_id[other_id]
                            for path in canonicalize_scope(
                                other.write_scope,
                                workspace_root=self._workspace_root,
                            ):
                                peer_paths.append(str(path.relative_to(root)))
                        parallel_authorized_by_id[peer_id] = tuple(
                            sorted(set(peer_paths))
                        )
                for subtask_id in group.subtask_ids:
                    if subtask_id in cancelled:
                        continue
                    subtask = by_id[subtask_id]
                    cancel_event = threading.Event()
                    run_key = f"{team_plan.task_id}:{subtask_id}"
                    with self._running_lock:
                        self._running[run_key] = _RunningSubtask(
                            attempt_id="", cancel_event=cancel_event
                        )
                    futures.append(
                        (
                            subtask_id,
                            pool.submit(
                                self._run_subtask,
                                subtask,
                                team_plan.task_id,
                                parallel_writers=parallel_writers,
                                parallel_authorized=parallel_authorized_by_id.get(
                                    subtask_id, ()
                                ),
                                cancel_event=cancel_event,
                                run_key=run_key,
                            ),
                        )
                    )
                for subtask_id, future in futures:
                    with self._running_lock:
                        if f"{team_plan.task_id}:{subtask_id}" in self._running:
                            self._running[f"{team_plan.task_id}:{subtask_id}"].future = future
                    outcome = future.result()
                    with self._running_lock:
                        self._running.pop(f"{team_plan.task_id}:{subtask_id}", None)
                    outcomes[subtask_id] = outcome
                    if outcome.state in (SubtaskState.FAILED, SubtaskState.CANCELLED):
                        cancel_dependents(subtask_id)

        return TeamRunResult(
            task_id=team_plan.task_id,
            outcomes=tuple(outcomes[subtask.subtask_id] for subtask in team_plan.subtasks),
            reason_codes=(),
        )

    def _build_resource_requests(
        self,
        subtask: SubtaskSpec,
        *,
        write_paths: tuple[Path, ...],
        read_paths: tuple[Path, ...],
        isolation: IsolationStrategy,
    ) -> tuple[ResourceRequest, ...]:
        requests: list[ResourceRequest] = []
        if subtask.operation_type in GLOBAL_OPERATION_TYPES:
            key = self._lease_manager.resource_key_for_global()
            requests.append(
                ResourceRequest(key=key, mode=AccessMode.GLOBAL, path=None)
            )
        for path in read_paths:
            canonical = self._lease_manager.canonicalize_path(path)
            key = self._lease_manager.resource_key_for_path(canonical, mode=AccessMode.READ)
            requests.append(
                ResourceRequest(key=key, mode=AccessMode.READ, path=canonical)
            )
        if isolation == IsolationStrategy.GRANULAR_LEASE:
            for path in write_paths:
                canonical = self._lease_manager.canonicalize_path(path)
                key = self._lease_manager.resource_key_for_path(
                    canonical, mode=AccessMode.WRITE
                )
                requests.append(
                    ResourceRequest(key=key, mode=AccessMode.WRITE, path=canonical)
                )
        return tuple(requests)

    def _authorized_relative_paths(
        self,
        write_paths: tuple[Path, ...],
        *,
        workspace: str,
    ) -> tuple[str, ...]:
        root = Path(os.path.realpath(workspace))
        main_root = Path(os.path.realpath(self._workspace_root))
        rel: list[str] = []
        for path in write_paths:
            try:
                rel.append(str(path.relative_to(main_root)))
            except ValueError:
                rel.append(str(path.relative_to(root)))
        return tuple(sorted(set(rel)))

    def _verify_changes(
        self,
        *,
        subtask: SubtaskSpec,
        isolation: IsolationStrategy,
        workspace: str,
        write_paths: tuple[Path, ...],
        before: dict,
        after: dict,
        main_before: dict | None,
        main_after: dict | None,
        executor_claimed: tuple[str, ...],
        full_workspace: bool,
        parallel_authorized: tuple[str, ...] = (),
    ) -> tuple[str, ...] | None:
        actual = diff_snapshots(before, after)
        peer_allowed = set(parallel_authorized)
        if isolation == IsolationStrategy.WORKTREE:
            if main_before is not None and main_after is not None:
                main_changed = filter_infrastructure_changes(
                    diff_snapshots(main_before, main_after)
                )
                if main_changed:
                    return None
            authorized = self._authorized_relative_paths(write_paths, workspace=workspace)
            if not authorized and not full_workspace:
                return tuple(actual) if actual else ()
            if not authorized and full_workspace:
                return tuple(actual)
            mapped = map_worktree_changes(
                workspace_root=self._workspace_root,
                worktree_root=workspace,
                authorized_relative=authorized,
                observed_relative=actual,
            )
            if full_workspace:
                return tuple(actual)
            allowed = set(authorized)
            if set(mapped) - allowed:
                return None
            if set(actual) - allowed:
                return None
            if set(executor_claimed) - allowed and executor_claimed:
                return None
            return mapped

        allowed = {
            str(path.relative_to(Path(os.path.realpath(workspace))))
            for path in write_paths
        }
        for changed in actual:
            if changed not in allowed and changed not in peer_allowed:
                return None
        if set(executor_claimed) - allowed and executor_claimed:
            return None
        return tuple(sorted(c for c in actual if c in allowed))

    def _capture_inventory(
        self,
        workspace: str,
        *,
        watch_paths: tuple[str, ...],
        full_workspace: bool,
    ):
        result = inventory_workspace(
            workspace,
            relative_paths=watch_paths,
            full_workspace=full_workspace,
        )
        if result.exceeded or not result.complete:
            raise HarmoniaError(REASON_INVENTORY_EXCEEDED)
        return result.snapshots

    def _release_reservation(
        self,
        reservation: _ActiveReservation | None,
        *,
        release_token_once,
    ) -> None:
        if reservation is None:
            return
        if (
            not reservation.terminated
            and not self._supervised.wait_attempt_terminated(reservation.attempt_id)
        ):
            return
        if reservation.requests:
            try:
                self._lease_manager.release_all(
                    reservation.requests, reservation.owner
                )
            except LeaseOwnershipError:
                pass
        release_token_once()

    def _run_subtask(
        self,
        subtask: SubtaskSpec,
        task_id: str,
        *,
        parallel_writers: bool,
        parallel_authorized: tuple[str, ...] = (),
        cancel_event: threading.Event,
        run_key: str,
    ) -> SubtaskOutcome:
        attempt_id = uuid.uuid4().hex[:16]
        with self._running_lock:
            if run_key in self._running:
                self._running[run_key].attempt_id = attempt_id
        owner = ResourceOwner(task_id, subtask.subtask_id, attempt_id)
        read_paths = canonicalize_scope(
            subtask.read_scope, workspace_root=self._workspace_root
        )
        write_paths = canonicalize_scope(
            subtask.write_scope, workspace_root=self._workspace_root
        )
        uncertain = not write_paths and subtask.operation_type == "file_edit"
        read_rel = tuple(str(p.relative_to(Path(self._workspace_root))) for p in read_paths)
        write_rel = tuple(str(p.relative_to(Path(self._workspace_root))) for p in write_paths)
        scope_enforcement = self._scope_enforcement.proves_exact_parallel_scope(
            task_id=task_id,
            subtask=subtask,
            read_paths=read_rel,
            write_paths=write_rel,
        )
        isolation = choose_isolation(
            subtask,
            write_paths=write_paths,
            uncertain_scope=uncertain,
            parallel_writers=parallel_writers,
            scope_enforcement=scope_enforcement,
        )

        if not self._seal_authorizer.authorize_subtask(
            task_id=task_id,
            subtask=subtask,
            read_paths=read_rel,
            write_paths=write_rel,
            isolation=isolation,
        ):
            return SubtaskOutcome(
                subtask_id=subtask.subtask_id,
                state=SubtaskState.FAILED,
                reason_codes=(REASON_UNAUTHORIZED,),
                isolation=isolation,
            )

        workspace = self._workspace_root
        worktree_path: str | None = None
        token: TokenReservation | None = None
        token_released = False
        reservation: _ActiveReservation | None = None
        heartbeat_stop = threading.Event()
        heartbeat_thread: threading.Thread | None = None
        terminated = False
        full_workspace = (
            isolation == IsolationStrategy.WORKTREE
            and not write_rel
            and subtask.operation_type in GLOBAL_OPERATION_TYPES
        )

        def release_token_once() -> None:
            nonlocal token_released
            if not token_released and token is not None:
                self._token_pool.release(token)
                token_released = True

        outcome: SubtaskOutcome | None = None
        cleanup_failed = False
        evidence_digest: str | None = None
        evidence_paths: tuple[str, ...] = ()
        exec_result = None
        try:
            token = self._token_pool.try_acquire(subtask.resources)
            if token is None:
                outcome = SubtaskOutcome(
                    subtask_id=subtask.subtask_id,
                    state=SubtaskState.FAILED,
                    reason_codes=(REASON_BUSY,),
                    isolation=isolation,
                )
            else:
                if isolation == IsolationStrategy.WORKTREE:
                    opaque = f"wt{attempt_id}"
                    worktree_path = self._worktree_authority.create_worktree(
                        repository_root=self._workspace_root,
                        base_ref="HEAD",
                        opaque_name=opaque,
                    )
                    workspace = worktree_path

                requests = self._build_resource_requests(
                    subtask,
                    write_paths=write_paths,
                    read_paths=read_paths,
                    isolation=isolation,
                )
                reservation = _ActiveReservation(
                    requests, owner, token, False, attempt_id, False
                )

                if requests:
                    ttl = min(
                        DEFAULT_TTL_S,
                        subtask.deadline_s or DEFAULT_TTL_S,
                    )
                    self._lease_manager.acquire_all(
                        requests,
                        owner,
                        timeout=LEASE_ACQUIRE_TIMEOUT_S,
                        ttl_s=ttl,
                    )

                    def heartbeat_loop() -> None:
                        interval = max(0.05, ttl / HEARTBEAT_INTERVAL_DIVISOR)
                        while not heartbeat_stop.wait(interval):
                            try:
                                self._lease_manager.heartbeat(
                                    requests, owner, ttl_s=ttl
                                )
                            except LeaseOwnershipError:
                                break

                    heartbeat_thread = threading.Thread(
                        target=heartbeat_loop, daemon=True, name="harmonia-heartbeat"
                    )
                    heartbeat_thread.start()

                watch_paths = write_rel if write_rel else read_rel
                main_before = None
                main_after = None
                if isolation == IsolationStrategy.WORKTREE:
                    main_before = self._capture_inventory(
                        self._workspace_root,
                        watch_paths=watch_paths or ("README.md",),
                        full_workspace=False,
                    )
                    before = self._capture_inventory(
                        workspace,
                        watch_paths=watch_paths or ("README.md",),
                        full_workspace=full_workspace,
                    )
                else:
                    before = self._capture_inventory(
                        workspace,
                        watch_paths=watch_paths,
                        full_workspace=full_workspace,
                    )

                exec_result = self._supervised.execute(
                    subtask=subtask,
                    workspace_root=workspace,
                    attempt_id=attempt_id,
                    cancel_event=cancel_event,
                )

                heartbeat_stop.set()
                if heartbeat_thread is not None:
                    heartbeat_thread.join(timeout=1.0)

                terminated = exec_result.kind not in (
                    ExecutionResultKind.NON_TERMINATED,
                )
                if exec_result.kind == ExecutionResultKind.NON_TERMINATED:
                    outcome = SubtaskOutcome(
                        subtask_id=subtask.subtask_id,
                        state=SubtaskState.FAILED,
                        reason_codes=(REASON_EXECUTOR_NON_TERMINATED,),
                        isolation=isolation,
                    )
                elif exec_result.kind == ExecutionResultKind.NON_CANCELLABLE:
                    outcome = SubtaskOutcome(
                        subtask_id=subtask.subtask_id,
                        state=SubtaskState.FAILED,
                        reason_codes=(REASON_EXECUTOR_NON_CANCELLABLE,),
                        isolation=isolation,
                    )
                elif exec_result.kind == ExecutionResultKind.TIMEOUT:
                    outcome = SubtaskOutcome(
                        subtask_id=subtask.subtask_id,
                        state=SubtaskState.FAILED,
                        reason_codes=(REASON_SUBTASK_TIMEOUT,),
                        isolation=isolation,
                    )
                elif exec_result.kind == ExecutionResultKind.CANCELLED:
                    outcome = SubtaskOutcome(
                        subtask_id=subtask.subtask_id,
                        state=SubtaskState.CANCELLED,
                        reason_codes=(REASON_SUBTASK_CANCELLED,),
                        isolation=isolation,
                    )
                elif exec_result.kind == ExecutionResultKind.FAILED:
                    outcome = SubtaskOutcome(
                        subtask_id=subtask.subtask_id,
                        state=SubtaskState.FAILED,
                        reason_codes=(REASON_SUBTASK_FAILED,),
                        isolation=isolation,
                    )
                else:
                    if isolation == IsolationStrategy.WORKTREE:
                        main_after = self._capture_inventory(
                            self._workspace_root,
                            watch_paths=watch_paths or ("README.md",),
                            full_workspace=False,
                        )
                        after = self._capture_inventory(
                            workspace,
                            watch_paths=watch_paths or ("README.md",),
                            full_workspace=full_workspace,
                        )
                    else:
                        after = self._capture_inventory(
                            workspace,
                            watch_paths=watch_paths,
                            full_workspace=full_workspace,
                        )

                    verified = self._verify_changes(
                        subtask=subtask,
                        isolation=isolation,
                        workspace=workspace,
                        write_paths=write_paths,
                        before=before,
                        after=after,
                        main_before=main_before,
                        main_after=main_after,
                        executor_claimed=exec_result.altered_paths,
                        full_workspace=full_workspace,
                        parallel_authorized=parallel_authorized,
                    )
                    if verified is None:
                        outcome = SubtaskOutcome(
                            subtask_id=subtask.subtask_id,
                            state=SubtaskState.FAILED,
                            reason_codes=(REASON_OUT_OF_SCOPE,),
                            isolation=isolation,
                            altered_paths=diff_snapshots(before, after),
                        )
                    elif exec_result.exit_code != 0:
                        outcome = SubtaskOutcome(
                            subtask_id=subtask.subtask_id,
                            state=SubtaskState.FAILED,
                            reason_codes=(REASON_SUBTASK_FAILED,),
                            isolation=isolation,
                            altered_paths=verified,
                        )
                    else:
                        changed = verified
                        if isolation == IsolationStrategy.WORKTREE and changed:
                            capture_deadline = time.monotonic() + EVIDENCE_CAPTURE_TIMEOUT_S
                            while time.monotonic() < capture_deadline:
                                try:
                                    evidence_digest, evidence_paths = compute_evidence_digest(
                                        workspace_root=workspace,
                                        relative_paths=changed,
                                    )
                                    break
                                except OSError:
                                    time.sleep(0.01)
                            else:
                                outcome = SubtaskOutcome(
                                    subtask_id=subtask.subtask_id,
                                    state=SubtaskState.FAILED,
                                    reason_codes=(REASON_EVIDENCE_CAPTURE_FAILED,),
                                    isolation=isolation,
                                    altered_paths=verified,
                                )
                        if outcome is None:
                            outcome = SubtaskOutcome(
                                subtask_id=subtask.subtask_id,
                                state=SubtaskState.COMPLETED,
                                reason_codes=(),
                                isolation=isolation,
                                altered_paths=verified,
                                evidence_digest=evidence_digest,
                                evidence_paths=evidence_paths,
                            )
        except LeaseAcquisitionTimeout:
            outcome = SubtaskOutcome(
                subtask_id=subtask.subtask_id,
                state=SubtaskState.FAILED,
                reason_codes=(REASON_BUSY,),
                isolation=isolation,
            )
        except HarmoniaError as exc:
            outcome = SubtaskOutcome(
                subtask_id=subtask.subtask_id,
                state=SubtaskState.FAILED,
                reason_codes=exc.reason_codes,
                isolation=isolation,
            )
        except WorktreeDeniedError as exc:
            outcome = SubtaskOutcome(
                subtask_id=subtask.subtask_id,
                state=SubtaskState.FAILED,
                reason_codes=exc.reason_codes,
                isolation=isolation,
            )
        except OSError:
            outcome = SubtaskOutcome(
                subtask_id=subtask.subtask_id,
                state=SubtaskState.FAILED,
                reason_codes=(REASON_SUBTASK_FAILED,),
                isolation=isolation,
            )
        finally:
            heartbeat_stop.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join(timeout=1.0)
            if exec_result is not None:
                terminated = exec_result.kind not in (
                    ExecutionResultKind.NON_TERMINATED,
                )
            should_release = exec_result is None or exec_result.kind != (
                ExecutionResultKind.NON_TERMINATED
            )
            if reservation is not None:
                reservation = _ActiveReservation(
                    reservation.requests,
                    reservation.owner,
                    reservation.token,
                    reservation.token_released,
                    reservation.attempt_id,
                    terminated,
                )
            if worktree_path is not None:
                cleanup_deadline = time.monotonic() + CLEANUP_TIMEOUT_S
                removed = False
                while time.monotonic() < cleanup_deadline:
                    try:
                        self._worktree_authority.remove_worktree(worktree_path)
                        removed = True
                        break
                    except HarmoniaError as exc:
                        if REASON_WORKTREE_CLEANUP_FAILED in exc.reason_codes:
                            cleanup_failed = True
                        break
                    except (WorktreeDeniedError, OSError):
                        cleanup_failed = True
                        break
                if not removed and not cleanup_failed:
                    cleanup_failed = True
            if reservation is not None and should_release:
                self._release_reservation(
                    reservation, release_token_once=release_token_once
                )
            elif token is not None and not token_released and should_release:
                release_token_once()

        if cleanup_failed:
            return SubtaskOutcome(
                subtask_id=subtask.subtask_id,
                state=SubtaskState.FAILED,
                reason_codes=(REASON_WORKTREE_CLEANUP_FAILED,),
                isolation=isolation,
            )
        assert outcome is not None
        return outcome


def _build_dependents(subtasks: tuple[SubtaskSpec, ...]) -> dict[str, tuple[str, ...]]:
    mapping: dict[str, list[str]] = {subtask.subtask_id: [] for subtask in subtasks}
    for subtask in subtasks:
        for dependency in subtask.dependencies:
            mapping[dependency].append(subtask.subtask_id)
    return {
        key: tuple(sorted(values)) for key, values in mapping.items() if values
    }
