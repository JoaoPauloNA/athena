"""Aceitação ROUTE-0 da autoridade Zeus/Nike no caminho MCP real."""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
from pathlib import Path

import pytest

from athena.bridge import RunRequest, RunResult
from athena.execution import CancellationToken, ExecutionState
from athena.mcp_server import MCPServer, MCPServerDependencies
from athena.mcp_stdio import MCPApplication
from athena.profiles import resolve_service_profile
from athena.registry import ExecutionRegistry
from athena.router import (
    ComboAttempt,
    ComboRequest,
    RoutingAbstained,
    RoutingContext,
)
from athena.routing_authority import DeterministicRoutingAuthority
from tests.route0_support import routing_arguments, write_route_config


class RecordingRouter:
    def __init__(self, cwd: Path) -> None:
        self.calls: list[ComboRequest] = []
        self._cwd = cwd

    def run(self, combo: ComboRequest, *, control: object = None) -> RunResult:
        self.calls.append(combo)
        return RunResult(
            ("recorded",), self._cwd, ExecutionState.COMPLETED, 0, "ok", "", 0.0
        )


def _context(**changes: object) -> RoutingContext:
    values: dict[str, object] = {
        "task_type": "backend",
        "primary_domain": "software.backend",
        "risk_level": "low",
        "required_capabilities": ("execute",),
    }
    values.update(changes)
    return RoutingContext(**values)  # type: ignore[arg-type]


def _attempt(provider: str, cwd: Path, marker: str | None = None) -> ComboAttempt:
    return ComboAttempt(
        provider,
        RunRequest((marker or provider,), cwd),
    )


def _combo(cwd: Path, providers: tuple[str, ...]) -> ComboRequest:
    return ComboRequest(tuple(_attempt(provider, cwd) for provider in providers), None)


def _server(config_dir: Path | None, cwd: Path) -> tuple[MCPServer, RecordingRouter]:
    router = RecordingRouter(cwd)
    server = MCPServer(
        MCPServerDependencies(
            router=router,
            registry=ExecutionRegistry(),
            verifier=lambda request, control: None,  # type: ignore[arg-type]
            profile_resolver=resolve_service_profile,
            control_factory=CancellationToken,
            routing_authority=DeterministicRoutingAuthority(config_dir),
        )
    )
    return server, router


@pytest.mark.parametrize(
    "changes",
    [
        {"task_type": "SENSITIVE TEXT"},
        {"primary_domain": "../../secret"},
        {"risk_level": "unknown"},
        {"required_capabilities": "execute"},
        {"required_capabilities": ("execute", "execute")},
        {"explicit_agent_tag": "bad tag"},
    ],
)
def test_context_validation_is_strict_and_sanitized(changes: dict[str, object]) -> None:
    with pytest.raises((ValueError, TypeError)) as raised:
        _context(**changes)
    assert str(raised.value) == "ROUTE_CONTEXT_INVALID"
    assert "SENSITIVE" not in str(raised.value)


def test_partial_context_fails_with_stable_sanitized_code(tmp_path: Path) -> None:
    server, _router = _server(None, tmp_path)
    arguments = {
        "attempts": [
            {"provider": "p", "command": ["SENSITIVE"], "cwd": str(tmp_path)}
        ],
        "task_type": "backend",
    }
    with pytest.raises(ValueError) as raised:
        MCPApplication(server).prepare_long_call("run_combo", arguments, "request")
    assert str(raised.value) == "ROUTE_CONTEXT_MISSING"
    assert "SENSITIVE" not in str(raised.value)


def test_same_snapshot_and_reordered_recipes_produce_same_plan(tmp_path: Path) -> None:
    config_dir = write_route_config(
        tmp_path / "config", providers=("a-selected", "z-advisory")
    )
    authority = DeterministicRoutingAuthority(config_dir)
    first = authority.plan(
        _combo(tmp_path, ("z-advisory", "a-selected")), _context()
    )
    second = authority.plan(
        _combo(tmp_path, ("a-selected", "z-advisory")), _context()
    )
    third = authority.plan(
        _combo(tmp_path, ("z-advisory", "a-selected")), _context()
    )
    assert first == second == third
    assert [attempt.provider for attempt in first.attempts] == ["a-selected"]


def test_one_advisory_candidate_cannot_force_selection_or_reach_runner(
    tmp_path: Path,
) -> None:
    config_dir = write_route_config(
        tmp_path / "config", providers=("a-selected", "z-client")
    )
    server, router = _server(config_dir, tmp_path)
    with pytest.raises(RoutingAbstained, match="^ROUTE_RECIPE_DIVERGENT$"):
        server.run_combo(
            _combo(tmp_path, ("z-client",)),
            request_id="single",
            routing_context=_context(),
        )
    assert router.calls == []


@pytest.mark.parametrize(
    ("providers", "reason"),
    [
        (("wrong",), "ROUTE_RECIPE_DIVERGENT"),
        (("a-selected", "a-selected"), "ROUTE_RECIPE_CONFLICT"),
    ],
)
def test_divergent_or_conflicting_recipe_never_reaches_runner(
    tmp_path: Path, providers: tuple[str, ...], reason: str
) -> None:
    config_dir = write_route_config(tmp_path / "config", providers=("a-selected",))
    server, router = _server(config_dir, tmp_path)
    combo = _combo(tmp_path, providers)
    if reason == "ROUTE_RECIPE_CONFLICT":
        combo = ComboRequest(
            (
                _attempt("a-selected", tmp_path, "one"),
                _attempt("a-selected", tmp_path, "two"),
            ),
            None,
        )
    with pytest.raises(RoutingAbstained, match=f"^{reason}$"):
        server.run_combo(combo, request_id=reason, routing_context=_context())
    assert router.calls == []


def test_missing_config_and_missing_context_never_reach_runner(tmp_path: Path) -> None:
    server, router = _server(None, tmp_path)
    with pytest.raises(RoutingAbstained, match="^ROUTE_CONTEXT_MISSING$"):
        server.run_combo(_combo(tmp_path, ("p",)), request_id="missing-context")
    with pytest.raises(RoutingAbstained, match="^ROUTE_CONFIG_UNAVAILABLE$"):
        server.run_combo(
            _combo(tmp_path, ("p",)),
            request_id="missing-config",
            routing_context=_context(),
        )
    assert router.calls == []


@pytest.mark.parametrize("target", ["config", "registry"])
def test_tampered_snapshot_never_reaches_runner(tmp_path: Path, target: str) -> None:
    config_dir = write_route_config(tmp_path / "config", providers=("a-selected",))
    server, router = _server(config_dir, tmp_path)
    server.run_combo(
        _combo(tmp_path, ("a-selected",)),
        request_id=f"warm-{target}",
        routing_context=_context(),
    )
    router.calls.clear()
    if target == "config":
        (config_dir / "providers.json").write_text("{}", encoding="utf-8")
        reason = "ROUTE_CONFIG_UNAVAILABLE"
    else:
        version = next(
            path
            for path in (config_dir / "zeus-registry").glob("*.json")
            if path.name != "current.json"
        )
        version.write_text("[]", encoding="utf-8")
        reason = "ROUTE_REGISTRY_UNAVAILABLE"
    with pytest.raises(RoutingAbstained, match=f"^{reason}$"):
        server.run_combo(
            _combo(tmp_path, ("a-selected",)),
            request_id=f"tampered-{target}",
            routing_context=_context(),
        )
    assert router.calls == []


@pytest.mark.parametrize(
    "fixture",
    [
        {"lifecycle": "suspended", "capabilities": ("execute",)},
        {"lifecycle": "approved", "capabilities": ("different",)},
    ],
)
def test_explicit_tag_cannot_bypass_lifecycle_or_capability(
    tmp_path: Path, fixture: dict[str, object]
) -> None:
    config_dir = write_route_config(
        tmp_path / "config",
        providers=("a-selected",),
        **fixture,  # type: ignore[arg-type]
    )
    server, router = _server(config_dir, tmp_path)
    with pytest.raises(RoutingAbstained, match="^ROUTE_NO_ELIGIBLE_PROVIDER$"):
        server.run_combo(
            _combo(tmp_path, ("a-selected",)),
            request_id="explicit-tag",
            routing_context=_context(explicit_agent_tag="route-agent"),
        )
    assert router.calls == []


def test_ask_provider_is_exact_direct_and_still_eligibility_controlled(
    tmp_path: Path,
) -> None:
    config_dir = write_route_config(
        tmp_path / "config", providers=("a-selected", "z-direct")
    )
    server, router = _server(config_dir, tmp_path)
    payload = server.ask_provider(
        _combo(tmp_path, ("a-selected", "z-direct")),
        request_id="direct-ok",
        provider_id="z-direct",
        routing_context=_context(),
    )
    assert payload["result"]["state"] == "completed"
    assert [attempt.provider for attempt in router.calls[0].attempts] == ["z-direct"]
    router.calls.clear()
    with pytest.raises(RoutingAbstained, match="^ROUTE_DIRECT_PROVIDER_DENIED$"):
        server.ask_provider(
            _combo(tmp_path, ("a-selected",)),
            request_id="direct-denied",
            provider_id="not-configured",
            routing_context=_context(),
        )
    assert router.calls == []


@pytest.mark.skipif(os.name != "posix", reason="real MCP child requires POSIX")
def test_real_jsonrpc_ignores_misleading_first_recipe_and_reaches_cap0(
    tmp_path: Path,
) -> None:
    config_dir = write_route_config(
        tmp_path / "config", providers=("a-selected", "z-misleading")
    )
    child_code = (
        "import os; "
        "ok=os.environ.get('ROUTE_VISIBLE')=='yes' and "
        "'ROUTE0_SECRET_TOKEN' not in os.environ and bool(os.environ.get('PATH')); "
        "print('ROUTE0_CAP0_OK' if ok else 'ROUTE0_CAP0_BAD')"
    )
    arguments = {
        **routing_arguments(),
        "attempts": [
            {
                "provider": "z-misleading",
                "command": [sys.executable, "-c", "print('MISLEADING_RAN')"],
                "cwd": str(tmp_path),
            },
            {
                "provider": "a-selected",
                "command": [sys.executable, "-c", child_code],
                "cwd": str(tmp_path),
                "env": {"ROUTE_VISIBLE": "yes"},
            },
        ],
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "athena"],
        cwd=Path(__file__).resolve().parents[1],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            **os.environ,
            "ATHENA_CONFIG_DIR": str(config_dir),
            "ROUTE0_SECRET_TOKEN": "not-for-child",
        },
    )
    try:
        assert process.stdin is not None and process.stdout is not None
        request = {
            "jsonrpc": "2.0",
            "id": "route-real",
            "method": "tools/call",
            "params": {"name": "run_combo", "arguments": arguments},
        }
        process.stdin.write(json.dumps(request) + "\n")
        process.stdin.flush()
        readable, _, _ = select.select([process.stdout], [], [], 10)
        assert readable
        response = json.loads(process.stdout.readline())
        payload = json.loads(response["result"]["content"][0]["text"])
        assert payload["result"]["state"] == "completed"
        assert "ROUTE0_CAP0_OK" in payload["result"]["stdout"]
        assert "MISLEADING_RAN" not in payload["result"]["stdout"]
    finally:
        if process.stdin is not None:
            process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
