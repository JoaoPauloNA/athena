"""Validação de DAG e ordenação topológica determinística."""

from __future__ import annotations

from .contracts import (
    REASON_CYCLE,
    REASON_DUPLICATE_ID,
    REASON_MISSING_DEPENDENCY,
    HarmoniaError,
    SubtaskSpec,
    TeamPlan,
)


def validate_dag(plan: TeamPlan) -> tuple[SubtaskSpec, ...]:
    """Validar aciclicidade, IDs únicos e dependências existentes."""
    seen: set[str] = set()
    ordered: list[SubtaskSpec] = []
    for subtask in plan.subtasks:
        if subtask.subtask_id in seen:
            raise HarmoniaError(REASON_DUPLICATE_ID)
        seen.add(subtask.subtask_id)
        ordered.append(subtask)

    ids = {subtask.subtask_id for subtask in ordered}
    for subtask in ordered:
        for dependency in subtask.dependencies:
            if dependency not in ids:
                raise HarmoniaError(REASON_MISSING_DEPENDENCY)

    indegree: dict[str, int] = {subtask.subtask_id: 0 for subtask in ordered}
    adjacency: dict[str, list[str]] = {subtask.subtask_id: [] for subtask in ordered}
    for subtask in ordered:
        for dependency in subtask.dependencies:
            adjacency[dependency].append(subtask.subtask_id)
            indegree[subtask.subtask_id] += 1

    ready = sorted(node for node, degree in indegree.items() if degree == 0)
    visited: list[str] = []
    while ready:
        current = ready.pop(0)
        visited.append(current)
        for neighbor in sorted(adjacency[current]):
            indegree[neighbor] -= 1
            if indegree[neighbor] == 0:
                ready.append(neighbor)
                ready.sort()

    if len(visited) != len(ordered):
        raise HarmoniaError(REASON_CYCLE)
    return tuple(ordered)
