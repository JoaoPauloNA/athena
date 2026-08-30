"""Fixtures mínimas para o snapshot interno exigido por ROUTE-0."""

from __future__ import annotations

import json
from pathlib import Path

from athena.config_loader import build_manifest, write_snapshot
from athena.zeus import AgentRecord, ZeusRegistry
from athena.zeus.persistence import save_registry


def write_route_config(
    config_dir: Path,
    *,
    providers: tuple[str, ...],
    capabilities: tuple[str, ...] = ("execute",),
    lifecycle: str = "approved",
) -> Path:
    config_dir.mkdir(parents=True, exist_ok=True)
    provider_doc = {
        provider: {
            "mode": "agent_cli",
            "runtime_class": "local",
            "enabled": True,
            "approved": True,
            "command": provider,
        }
        for provider in providers
    }
    (config_dir / "providers.json").write_text(
        json.dumps(provider_doc, sort_keys=True), encoding="utf-8"
    )
    (config_dir / "functions.json").write_text("{}", encoding="utf-8")
    write_snapshot(config_dir, build_manifest(config_dir))

    registry = ZeusRegistry()
    registry.create_version(
        [
            AgentRecord(
                "route-agent",
                "software.backend",
                "fixture",
                frozenset(capabilities),
                "local",
                lifecycle=lifecycle,
            )
        ],
        action="create",
    )
    save_registry(registry, config_dir)
    cache_dir = config_dir / "cache"
    cache_dir.mkdir()
    (cache_dir / "inventory.json").write_text(
        json.dumps(
            {
                "entries": [
                    {"provider_id": provider, "healthy": True}
                    for provider in providers
                ]
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return config_dir


def routing_arguments(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "task_type": "backend",
        "primary_domain": "software.backend",
        "risk_level": "low",
        "required_capabilities": ["execute"],
    }
    values.update(changes)
    return values
