"""Seleção determinística de estratégia de isolamento."""

from __future__ import annotations

from pathlib import Path

from .contracts import (
    GLOBAL_OPERATION_TYPES,
    MAX_GRANULAR_WRITE_PATHS,
    IsolationStrategy,
    SubtaskSpec,
)


def choose_isolation(
    subtask: SubtaskSpec,
    *,
    write_paths: tuple[Path, ...],
    uncertain_scope: bool,
    parallel_writers: bool = False,
    scope_enforcement: bool = False,
) -> IsolationStrategy:
    if subtask.operation_type in GLOBAL_OPERATION_TYPES:
        return IsolationStrategy.WORKTREE
    if uncertain_scope:
        return IsolationStrategy.WORKTREE
    if len(write_paths) > MAX_GRANULAR_WRITE_PATHS:
        return IsolationStrategy.WORKTREE
    if parallel_writers and not scope_enforcement:
        return IsolationStrategy.WORKTREE
    if write_paths:
        return IsolationStrategy.GRANULAR_LEASE
    return IsolationStrategy.GRANULAR_LEASE
