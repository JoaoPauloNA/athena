"""Planejamento determinístico de grupos paralelos e serialização."""

from __future__ import annotations

from pathlib import Path

from .contracts import (
    CONTRACT_VERSION,
    GLOBAL_OPERATION_TYPES,
    REASON_BUSY,
    SCHEMA_VERSION,
    ExecutionPlan,
    HarmoniaError,
    ScheduleGroup,
    SubtaskSpec,
    TeamPlan,
)
from .dag import validate_dag
from .paths import canonicalize_scope
from .resources import TokenPoolLimits


def _paths_overlap(left: Path, right: Path) -> bool:
    if left == right:
        return True
    try:
        left.relative_to(right)
        return True
    except ValueError:
        pass
    try:
        right.relative_to(left)
        return True
    except ValueError:
        return False


def _subtask_paths(subtask: SubtaskSpec, *, workspace_root: str) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    read_paths = canonicalize_scope(subtask.read_scope, workspace_root=workspace_root)
    write_paths = canonicalize_scope(subtask.write_scope, workspace_root=workspace_root)
    return read_paths, write_paths


def _scope_conflict(a: SubtaskSpec, b: SubtaskSpec, *, workspace_root: str) -> bool:
    a_read, a_write = _subtask_paths(a, workspace_root=workspace_root)
    b_read, b_write = _subtask_paths(b, workspace_root=workspace_root)
    a_all = a_read + a_write
    b_all = b_read + b_write
    for left in a_all:
        for right in b_all:
            if _paths_overlap(left, right):
                return True
    return False


def _token_fits(
    group: tuple[SubtaskSpec, ...],
    candidate: SubtaskSpec,
    *,
    limits: TokenPoolLimits,
) -> bool:
    totals = [0, 0, 0, 0]
    for subtask in group + (candidate,):
        totals[0] += subtask.resources.cpu_tokens
        totals[1] += subtask.resources.ram_mb
        totals[2] += subtask.resources.gpu_tokens
        totals[3] += subtask.resources.provider_tokens
    return (
        totals[0] <= limits.cpu_tokens
        and totals[1] <= limits.ram_mb
        and totals[2] <= limits.gpu_tokens
        and totals[3] <= limits.provider_tokens
    )


def _conflicts_with_group(
    candidate: SubtaskSpec,
    group: tuple[SubtaskSpec, ...],
    *,
    workspace_root: str,
) -> str | None:
    for member in group:
        if (
            candidate.operation_type in GLOBAL_OPERATION_TYPES
            and member.operation_type in GLOBAL_OPERATION_TYPES
        ):
            return "global_exclusive"
        if candidate.operation_type in GLOBAL_OPERATION_TYPES or member.operation_type in GLOBAL_OPERATION_TYPES:
            return "global_exclusive"
        if _scope_conflict(candidate, member, workspace_root=workspace_root):
            return "scope_conflict"
    return None


def _subtask_fits_limits(subtask: SubtaskSpec, limits: TokenPoolLimits) -> bool:
    return (
        subtask.resources.cpu_tokens <= limits.cpu_tokens
        and subtask.resources.ram_mb <= limits.ram_mb
        and subtask.resources.gpu_tokens <= limits.gpu_tokens
        and subtask.resources.provider_tokens <= limits.provider_tokens
    )


def build_execution_plan(
    plan: TeamPlan,
    *,
    workspace_root: str,
    max_workers: int,
    token_limits: TokenPoolLimits | None = None,
) -> ExecutionPlan:
    """Construir plano explicável com ondas paralelas determinísticas."""
    limits = token_limits or TokenPoolLimits()
    ordered = validate_dag(plan)
    for subtask in ordered:
        if not _subtask_fits_limits(subtask, limits):
            raise HarmoniaError(REASON_BUSY)
    by_id = {subtask.subtask_id: subtask for subtask in ordered}
    remaining = {subtask.subtask_id for subtask in ordered}
    satisfied: set[str] = set()
    groups: list[ScheduleGroup] = []
    group_index = 0

    while remaining:
        ready = sorted(
            subtask_id
            for subtask_id in remaining
            if all(dep in satisfied for dep in by_id[subtask_id].dependencies)
        )
        if not ready:
            break

        parallel_cap = min(
            plan.max_parallelism,
            plan.project_parallelism,
            plan.aegis_parallelism,
            max_workers,
        )

        wave: list[str] = []
        wave_specs: list[SubtaskSpec] = []
        deferred_reason: dict[str, str] = {}

        for subtask_id in ready:
            if len(wave) >= parallel_cap:
                deferred_reason[subtask_id] = "parallel_limit"
                continue
            candidate = by_id[subtask_id]
            conflict = _conflicts_with_group(
                candidate,
                tuple(wave_specs),
                workspace_root=workspace_root,
            )
            if conflict is not None:
                deferred_reason[subtask_id] = conflict
                continue
            if not _token_fits(tuple(wave_specs), candidate, limits=limits):
                deferred_reason[subtask_id] = "token_budget"
                continue
            wave.append(subtask_id)
            wave_specs.append(candidate)

        if not wave:
            subtask_id = ready[0]
            wave = [subtask_id]
            wave_specs = [by_id[subtask_id]]
            reason = deferred_reason.get(subtask_id, "sequential_ready")
        elif len(wave) == 1:
            reason = deferred_reason.get(wave[0], "sequential_ready")
        else:
            reason = "independent_ready"

        groups.append(
            ScheduleGroup(
                group_id=f"g{group_index:03d}",
                subtask_ids=tuple(wave),
                parallelism_reason=reason,
            )
        )
        group_index += 1
        for subtask_id in wave:
            remaining.remove(subtask_id)
            satisfied.add(subtask_id)

    return ExecutionPlan(
        schema_version=SCHEMA_VERSION,
        contract_version=CONTRACT_VERSION,
        task_id=plan.task_id,
        groups=tuple(groups),
        reason_codes=(),
    )
