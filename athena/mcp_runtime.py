"""Composição concreta do servidor MCP modular para execução por stdio."""

from __future__ import annotations

import sys

from athena.bridge import LocalBridgeRunner
from athena.execution import CancellationToken
from athena.lease import DirectoryLeaseManager
from athena.mcp_server import MCPServer, MCPServerDependencies
from athena.mcp_stdio import JsonRpcStdioServer, MCPApplication, StdioTransport
from athena.profiles import resolve_service_profile
from athena.registry import ExecutionRegistry
from athena.router import ComboRouter
from athena.verifier import verify


def build_stdio_server(
    transport: StdioTransport | None = None,
) -> JsonRpcStdioServer:
    """Montar as implementações concretas fora do pacote fechado mcp_server."""
    registry = ExecutionRegistry()
    core = MCPServer(
        MCPServerDependencies(
            router=ComboRouter(LocalBridgeRunner(), DirectoryLeaseManager()),
            registry=registry,
            verifier=verify,
            profile_resolver=resolve_service_profile,
            control_factory=CancellationToken,
        )
    )
    streams = transport or StdioTransport(sys.stdin, sys.stdout, sys.stderr)
    return JsonRpcStdioServer(MCPApplication(core), streams)


def main() -> None:
    build_stdio_server().serve()


if __name__ == "__main__":
    main()
