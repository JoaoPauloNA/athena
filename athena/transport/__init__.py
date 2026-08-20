"""Transportes remotos do Athena MCP."""

from .contracts import (
    RemoteExecutionResult,
    RemoteProcessOutcome,
    RemoteRunner,
    SSHKeyAuthentication,
)
from .remote import RemoteExecutor, execute_remote
from .ssh import SSHCommandBuilder, build_ssh_command

__all__ = [
    "RemoteExecutionResult",
    "RemoteExecutor",
    "RemoteProcessOutcome",
    "RemoteRunner",
    "SSHCommandBuilder",
    "SSHKeyAuthentication",
    "build_ssh_command",
    "execute_remote",
]
