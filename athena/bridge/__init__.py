"""Ponte local entre processos e o ciclo de execução do Athena MCP."""

from .contracts import BridgeRunnerContract, RunRequest, RunResult
from .runner import LocalBridgeRunner

__all__ = [
    "BridgeRunnerContract",
    "LocalBridgeRunner",
    "RunRequest",
    "RunResult",
]
