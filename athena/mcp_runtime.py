"""Composição concreta do servidor MCP modular para execução por stdio."""

from __future__ import annotations

import atexit
import os
import secrets
import sys
from pathlib import Path

from athena.bridge import LocalBridgeRunner
from athena.clio import build_clio_emitter
from athena.execution import CancellationToken
from athena.flow.controller import make_flow_controller
from athena.iris import LocalIrisBoundary
from athena.lease import DirectoryLeaseManager
from athena.mcp_server import MCPServer, MCPServerDependencies
from athena.mcp_stdio import JsonRpcStdioServer, MCPApplication, StdioTransport
from athena.observation import ShadowEmitter
from athena.profiles import resolve_service_profile
from athena.registry import ExecutionRegistry
from athena.router import ComboRouter
from athena.routing_authority import DeterministicRoutingAuthority
from athena.tasks import SQLiteTaskStore
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
    # EG-3A: finalizador e sink local exigem opt-in e diretório explícitos.
    # A composição conhece as implementações; o server recebe só contratos.
    artifact_finalizer = None
    artifact_sink = None
    if os.environ.get("ATHENA_EG3A") == "1":
        from athena.evidence_gate.pipeline_eg3a import finalize_artifact
        from athena.evidence_gate.sink import AtomicJsonFileSink

        sink_directory = os.environ.get("ATHENA_EG3A_SINK_DIR")
        if sink_directory:
            try:
                artifact_sink = AtomicJsonFileSink(sink_directory)
            except (OSError, ValueError):
                artifact_sink = None
        if artifact_sink is not None:
            def artifact_finalizer(envelope: dict) -> dict:  # noqa: E704
                return finalize_artifact(envelope, opt_in=True)
    iris = LocalIrisBoundary(LocalBridgeRunner(), secrets.token_bytes(32))
    config_dir_value = os.environ.get("ATHENA_CONFIG_DIR")
    config_dir = None
    if config_dir_value:
        candidate = os.path.abspath(config_dir_value)
        config_dir = Path(candidate)

    # FLOW-1: compose FlowController wrapping the same task_store instance
    task_store = SQLiteTaskStore()
    flow_controller = make_flow_controller(task_store)
    clio_emitter = build_clio_emitter()
    if clio_emitter is not None:
        atexit.register(clio_emitter.shutdown)

    core = MCPServer(
        MCPServerDependencies(
            router=ComboRouter(
                iris,
                DirectoryLeaseManager(),
                attempt_authorizer=iris,
            ),
            registry=registry,
            verifier=verify,
            profile_resolver=resolve_service_profile,
            control_factory=CancellationToken,
            shadow_emitter=shadow,
            clio_emitter=clio_emitter,
            artifact_finalizer=artifact_finalizer,
            artifact_sink=artifact_sink,
            task_store=task_store,
            routing_authority=DeterministicRoutingAuthority(config_dir),
            flow_controller=flow_controller,
        )
    )
    streams = transport or StdioTransport(sys.stdin, sys.stdout, sys.stderr)
    return JsonRpcStdioServer(MCPApplication(core), streams)


def main() -> None:
    build_stdio_server().serve()


if __name__ == "__main__":
    main()
