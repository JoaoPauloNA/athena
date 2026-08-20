"""Primitivas de execução do Athena MCP."""

from .contracts import (
    TERMINAL_STATES,
    Clock,
    DeadlineKind,
    ExecutionControl,
    ExecutionDeadlines,
    ExecutionState,
)
from .lifecycle import (
    CancellationToken,
    ExecutionRecord,
    InvalidStateTransition,
    SystemClock,
    normalize_cancel_reason,
)

__all__ = [
    "TERMINAL_STATES",
    "CancellationToken",
    "Clock",
    "DeadlineKind",
    "ExecutionControl",
    "ExecutionDeadlines",
    "ExecutionRecord",
    "ExecutionState",
    "InvalidStateTransition",
    "SystemClock",
    "normalize_cancel_reason",
]
