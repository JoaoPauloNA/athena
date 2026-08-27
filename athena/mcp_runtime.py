"""Composição concreta do servidor MCP modular para execução por stdio."""

from __future__ import annotations

import os
import sys

from athena.bridge import LocalBridgeRunner
from athena.execution import CancellationToken
from athena.lease import DirectoryLeaseManager
from athena.mcp_server import MCPServer, MCPServerDependencies
from athena.mcp_stdio import JsonRpcStdioServer, MCPApplication, StdioTransport
from athena.observation import ShadowEmitter
from athena.profiles import resolve_service_profile
from athena.registry import ExecutionRegistry
from athena.router import ComboRouter
from athena.verifier import verify


def build_stdio_server(
    transport: StdioTransport | None = None,
    *,
    shadow_observer=None,
) -> JsonRpcStdioServer:
    """Montar as implementações concretas fora do pacote fechado mcp_server.

    A observação sombra é opt-in: só fica ativa com ATHENA_MOIRAS_SHADOW=1
    e um observer injetado. Sem isso, zero custo.
    """
    registry = ExecutionRegistry()
    shadow = ShadowEmitter(shadow_observer) if shadow_observer is not None else None
    # EG-3A: finalizador injetado APENAS com opt-in explícito (env).
    # A composição conhece o motor; o server recebe só o callable.
    artifact_finalizer = None
    if os.environ.get("ATHENA_EG3A") == "1":
        from athena.evidence_gate.pipeline_eg3a import finalize_artifact

        def artifact_finalizer(envelope: dict) -> dict:  # noqa: E704
            return finalize_artifact(envelope, opt_in=True)
    core = MCPServer(
        MCPServerDependencies(
            router=ComboRouter(LocalBridgeRunner(), DirectoryLeaseManager()),
            registry=registry,
            verifier=verify,
            profile_resolver=resolve_service_profile,
            control_factory=CancellationToken,
            shadow_emitter=shadow,
            artifact_finalizer=artifact_finalizer,
        )
    )
    streams = transport or StdioTransport(sys.stdin, sys.stdout, sys.stderr)
    return JsonRpcStdioServer(MCPApplication(core), streams)


def main() -> None:
    build_stdio_server().serve()


if __name__ == "__main__":
    main()
