"""Testes da camada fina de exposicao MCP."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from athena.bridge import RunRequest, RunResult
from athena.execution import CancellationToken, ExecutionState
from athena.mcp_server import (
    TOOL_NAMES,
    MCPServer,
    MCPServerContract,
    MCPServerDependencies,
    ask_provider,
    cancel_execution,
    get_execution,
    list_executions,
    run_combo,
)
from athena.profiles import ServiceProfile, resolve_service_profile
from athena.registry import ExecutionRegistry
from athena.router import ComboAttempt, ComboRequest


def _result(tmp_path: Path) -> RunResult:
    return RunResult(
        ("simulated",),
        tmp_path,
        ExecutionState.COMPLETED,
        0,
        "ok",
        "",
        0.01,
    )


class RecordingRouter:
    def __init__(self, result: RunResult) -> None:
        self.result = result
        self.calls: list[tuple[ComboRequest, object]] = []

    def run(self, combo: ComboRequest, *, control: object = None) -> RunResult:
        self.calls.append((combo, control))
        return self.result


class RecordingVerifier:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object]] = []

    def __call__(self, request: object, *, control: object) -> object:
        self.calls.append((request, control))
        phase = SimpleNamespace(execution={"state": "completed"})
        return SimpleNamespace(accepted=True, deterministic=phase, advisory=phase)


def _combo(tmp_path: Path, *, execution_id: str | None = None) -> ComboRequest:
    return ComboRequest(
        attempts=(
            ComboAttempt("simulated", RunRequest(("simulated",), tmp_path)),
        ),
        profile=None,
        execution_id=execution_id,
    )


def _server(tmp_path: Path) -> tuple[MCPServer, RecordingRouter, RecordingVerifier]:
    router = RecordingRouter(_result(tmp_path))
    verifier = RecordingVerifier()
    server = MCPServer(
        MCPServerDependencies(
            router=router,
            registry=ExecutionRegistry(),
            verifier=verifier,
            profile_resolver=resolve_service_profile,
            control_factory=CancellationToken,
        )
    )
    return server, router, verifier


def test_minimum_tool_surface_is_public_and_directly_invocable(tmp_path: Path) -> None:
    server, _, _ = _server(tmp_path)
    combo = _combo(tmp_path, execution_id="execution-direct")

    assert TOOL_NAMES == (
        "run_combo",
        "ask_provider",
        "get_execution",
        "list_executions",
        "cancel_execution",
        "submit_task",
        "get_task",
    )
    assert run_combo(server, combo, request_id="request-direct")["result"][
        "state"
    ] == "completed"
    assert get_execution(server, "execution-direct")["execution"] is not None
    assert len(list_executions(server)["executions"]) == 1


def test_run_combo_delegates_router_registry_and_verifier(tmp_path: Path) -> None:
    server, router, verifier = _server(tmp_path)
    verification = object()

    payload = server.run_combo(
        _combo(tmp_path, execution_id="execution-delegated"),
        request_id="request-delegated",
        verification=verification,  # type: ignore[arg-type]
    )

    assert payload["verification"]["accepted"] is True
    assert router.calls[0][0].execution_id == "execution-delegated"
    assert verifier.calls == [(verification, router.calls[0][1])]
    assert server.get_execution("execution-delegated")["execution"]["finalized"]


def test_prepared_execution_is_reused_without_second_registration(tmp_path: Path) -> None:
    server, router, _ = _server(tmp_path)
    prepared = server.prepare_execution(
        "run_combo", request_id="prepared-request", execution_id="prepared-execution"
    )

    payload = server.run_combo(
        _combo(tmp_path), request_id="prepared-request", prepared=prepared
    )

    assert payload["execution_id"] == "prepared-execution"
    assert router.calls[0][0].execution_id == "prepared-execution"
    assert len(server.list_executions()["executions"]) == 1


def test_ask_provider_resolves_profile_then_uses_same_router_path(tmp_path: Path) -> None:
    server, router, _ = _server(tmp_path)

    payload = ask_provider(
        server,
        _combo(tmp_path),
        request_id=91,
        provider_id="ollama",
    )

    assert payload["result"]["state"] == "completed"
    assert router.calls[0][0].profile is ServiceProfile.LOCAL_MODEL
    execution = server.get_execution(request_id=91)["execution"]
    assert execution["tool"] == "ask_provider"


def test_cancel_is_identical_twice_and_accepts_raw_request_id(tmp_path: Path) -> None:
    server, _, _ = _server(tmp_path)
    raw_request_id = "raw-request-id-with-private-entropy-0123456789"
    combo = _combo(tmp_path, execution_id="execution-cancel")
    server.run_combo(combo, request_id=raw_request_id)

    first = cancel_execution(server, request_id=raw_request_id, reason="user_requested")
    second = cancel_execution(server, "execution-cancel", reason="different")

    assert first == second == {
        "found": True,
        "requested": True,
        "execution_id": "execution-cancel",
    }
    assert server.get_execution(request_id=raw_request_id)["execution"] is not None


def test_public_server_satisfies_contract(tmp_path: Path) -> None:
    server, _, _ = _server(tmp_path)
    assert isinstance(server, MCPServerContract)


def test_mcp_server_imports_only_authorized_core_packages() -> None:
    package = Path(__file__).resolve().parents[1] / "athena" / "mcp_server"
    imports: set[str] = set()
    for module in package.glob("*.py"):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imports.add(node.module)
            elif isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)

    assert {
        name.split(".")[1]
        for name in imports
        if name.startswith("athena.")
    } <= {"router", "registry", "verifier", "execution", "profiles", "tasks", "flow"}


def test_new_core_contains_no_legacy_reference() -> None:
    package = Path(__file__).resolve().parents[1] / "athena"
    offenders = [
        path
        for path in package.rglob("*.py")
        if "legado" in path.read_text(encoding="utf-8").lower()
    ]
    assert offenders == []
