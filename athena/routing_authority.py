"""Autoridade composta ROUTE-0 para o caminho MCP de produção."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from athena.config_loader import ConfigLoadError, ConfigSnapshotCache
from athena.router import ComboRequest, RoutingAbstained, RoutingContext
from athena.zeus.contracts import TaskRequest
from athena.zeus.persistence import ConfigLoadError as RegistryLoadError
from athena.zeus.persistence import ZeusRegistrySnapshotCache
from athena.zeus.realign import NikeRuntimeSelector, ZeusEligibilityRouter

ROUTE_CONTEXT_MISSING = "ROUTE_CONTEXT_MISSING"
ROUTE_CONFIG_UNAVAILABLE = "ROUTE_CONFIG_UNAVAILABLE"
ROUTE_REGISTRY_UNAVAILABLE = "ROUTE_REGISTRY_UNAVAILABLE"
ROUTE_NO_ELIGIBLE_PROVIDER = "ROUTE_NO_ELIGIBLE_PROVIDER"
ROUTE_RECIPE_MISSING = "ROUTE_RECIPE_MISSING"
ROUTE_RECIPE_DIVERGENT = "ROUTE_RECIPE_DIVERGENT"
ROUTE_RECIPE_CONFLICT = "ROUTE_RECIPE_CONFLICT"
ROUTE_DIRECT_PROVIDER_DENIED = "ROUTE_DIRECT_PROVIDER_DENIED"


class DeterministicRoutingAuthority:
    """Resolver Zeus/Nike sem rede, descoberta, LLM ou subprocesso."""

    def __init__(self, config_dir: Path | None) -> None:
        self._config_dir = config_dir
        self._config_cache = (
            ConfigSnapshotCache(config_dir) if config_dir is not None else None
        )
        self._registry_cache = (
            ZeusRegistrySnapshotCache(config_dir) if config_dir is not None else None
        )

    def plan(
        self,
        combo: ComboRequest,
        context: RoutingContext | None,
        *,
        direct_provider_id: str | None = None,
    ) -> ComboRequest:
        if context is None:
            raise RoutingAbstained(ROUTE_CONTEXT_MISSING)
        if self._config_cache is None or self._registry_cache is None:
            raise RoutingAbstained(ROUTE_CONFIG_UNAVAILABLE)
        try:
            snapshot = self._config_cache.refresh()
        except (ConfigLoadError, OSError, ValueError):
            raise RoutingAbstained(ROUTE_CONFIG_UNAVAILABLE) from None
        try:
            registry = self._registry_cache.refresh()
        except (RegistryLoadError, OSError, ValueError, RuntimeError):
            raise RoutingAbstained(ROUTE_REGISTRY_UNAVAILABLE) from None

        request = TaskRequest(
            task_type=context.task_type,
            primary_domain=context.primary_domain,
            risk_level=context.risk_level,
            required_capabilities=context.required_capabilities,
            explicit_agent_tag=context.explicit_agent_tag,
        )
        try:
            decision = NikeRuntimeSelector(
                ZeusEligibilityRouter(registry),
                snapshot["providers"],
                self._config_dir / "cache",
            ).resolve(request, direct_provider_id=direct_provider_id)
        except (KeyError, TypeError, ValueError, OSError):
            raise RoutingAbstained(ROUTE_CONFIG_UNAVAILABLE) from None

        if decision.abstained or decision.provider_id is None:
            reason = (
                ROUTE_DIRECT_PROVIDER_DENIED
                if direct_provider_id is not None
                else ROUTE_NO_ELIGIBLE_PROVIDER
            )
            raise RoutingAbstained(reason)
        if direct_provider_id is not None and decision.provider_id != direct_provider_id:
            raise RoutingAbstained(ROUTE_DIRECT_PROVIDER_DENIED)

        matches = tuple(
            attempt for attempt in combo.attempts
            if attempt.provider == decision.provider_id
        )
        if not matches:
            reason = (
                ROUTE_RECIPE_DIVERGENT
                if combo.attempts
                else ROUTE_RECIPE_MISSING
            )
            raise RoutingAbstained(reason)
        first = matches[0]
        if any(attempt != first for attempt in matches[1:]):
            raise RoutingAbstained(ROUTE_RECIPE_CONFLICT)
        return replace(combo, attempts=(first,))
