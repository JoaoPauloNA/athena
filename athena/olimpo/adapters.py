"""Adaptadores opt-in de composição — expõem estado real ou ficam indisponíveis."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from athena.config_loader import (
    CONFIG_SCHEMA_VERSION,
    ConfigLoadError,
    build_manifest,
    load_config,
    load_observed,
    part_hash,
    write_snapshot,
)

from .contracts import (
    CAPABILITY_IMPLEMENTED,
    ClioStatusSnapshot,
    ConfigApplyResult,
    ConfigPreviewResult,
    ConfigSnapshotStatus,
    InventoryEntry,
    OlimpoDependencies,
    TaskReader,
    validate_allowed_origins,
)


def _snapshot_hash(config_dir: Path) -> tuple[bool, str | None]:
    snapshot = config_dir / "snapshot.json"
    try:
        content = snapshot.read_bytes()
    except OSError:
        return False, None
    return True, hashlib.sha256(content).hexdigest()


def _manifest_changes(
    current: dict[str, Any] | None,
    proposed: dict[str, Any],
) -> tuple[str, ...]:
    if current is None:
        parts = sorted(proposed.get("parts", {}))
        extras = sorted(proposed.get("extras", {}))
        return tuple(f"parts.{name}" for name in parts) + tuple(
            f"extras.{name}" for name in extras
        )
    changes: list[str] = []
    for section in ("parts", "extras"):
        current_section = current.get(section, {})
        proposed_section = proposed.get(section, {})
        if not isinstance(current_section, dict) or not isinstance(proposed_section, dict):
            continue
        names = sorted(set(current_section) | set(proposed_section))
        for name in names:
            if current_section.get(name) != proposed_section.get(name):
                changes.append(f"{section}.{name}")
    return tuple(changes)


class RegistryExecutionReader:
    """Projeta execuções sanitizadas do registro público em memória."""

    def __init__(self, registry: object) -> None:
        self._registry = registry

    def get_execution(self, execution_id: str) -> object | None:
        return self._registry.get(execution_id)  # type: ignore[union-attr]

    def list_executions(self, *, limit: int) -> list[object]:
        return self._registry.list(limit=limit)  # type: ignore[union-attr]


class ClioEmitterReader:
    """Status técnico do emissor Clio — sem conteúdo de evento."""

    def __init__(self, emitter: object) -> None:
        self._emitter = emitter

    def read_status(self) -> ClioStatusSnapshot:
        counters_obj = self._emitter.counters  # type: ignore[union-attr]
        counters = {
            name: int(getattr(counters_obj, name))
            for name in (
                "enqueued",
                "dropped_queue_full",
                "dropped_invalid",
                "writer_failures",
                "none_bypass",
            )
            if isinstance(getattr(counters_obj, name, None), int)
        }
        storage = (
            "available"
            if self._emitter.store.db_path.exists()  # type: ignore[union-attr]
            else "unavailable"
        )
        return ClioStatusSnapshot(
            level=self._emitter.level,  # type: ignore[union-attr]
            storage=storage,
            counters=counters,
        )


class ConfigDirectoryReader:
    """Snapshot de configuração somente leitura."""

    def __init__(self, config_dir: Path) -> None:
        self._config_dir = config_dir

    def read_status(self) -> ConfigSnapshotStatus:
        available, current_hash = _snapshot_hash(self._config_dir)
        return ConfigSnapshotStatus(
            available=available,
            current_hash=current_hash,
            schema_version=CONFIG_SCHEMA_VERSION if available else None,
        )


class ConfigDirectoryValidator:
    """Validação/preview via APIs públicas de config_loader."""

    def __init__(self, config_dir: Path) -> None:
        self._config_dir = config_dir

    def preview(
        self,
        manifest: dict[str, Any],
        *,
        expected_hash: str | None,
    ) -> ConfigPreviewResult:
        _, current_hash = _snapshot_hash(self._config_dir)
        if expected_hash is not None and current_hash != expected_hash:
            return ConfigPreviewResult(
                ok=False,
                reason_code="OLIMPO_CONFIG_CONFLICT",
                current_hash=current_hash,
            )
        if not _validate_proposed_manifest(self._config_dir, manifest):
            return ConfigPreviewResult(
                ok=False,
                reason_code="OLIMPO_CONFIG_VALIDATION_FAILED",
                current_hash=current_hash,
            )
        try:
            rebuilt = build_manifest(self._config_dir)
        except ConfigLoadError:
            rebuilt = None
        changes = _manifest_changes(rebuilt, manifest)
        proposed_hash = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return ConfigPreviewResult(
            ok=True,
            current_hash=current_hash,
            proposed_hash=proposed_hash,
            changes=changes,
            validation_status="valid",
        )


class ConfigDirectoryPublisher:
    """Publicação atômica com compare-and-swap por hash."""

    def __init__(self, config_dir: Path) -> None:
        self._config_dir = config_dir

    def apply(
        self,
        manifest: dict[str, Any],
        *,
        expected_hash: str,
    ) -> ConfigApplyResult:
        available, current_hash = _snapshot_hash(self._config_dir)
        if not available or current_hash != expected_hash:
            return ConfigApplyResult(
                ok=False,
                reason_code="OLIMPO_CONFIG_CONFLICT",
                current_hash=current_hash,
            )
        if not _validate_proposed_manifest(self._config_dir, manifest):
            return ConfigApplyResult(
                ok=False,
                reason_code="OLIMPO_CONFIG_VALIDATION_FAILED",
                current_hash=current_hash,
            )
        try:
            write_snapshot(self._config_dir, manifest)
        except ConfigLoadError:
            return ConfigApplyResult(
                ok=False,
                reason_code="OLIMPO_CONFIG_PUBLISH_FAILED",
                current_hash=current_hash,
            )
        _, applied_hash = _snapshot_hash(self._config_dir)
        return ConfigApplyResult(ok=True, applied_hash=applied_hash)


class InventorySnapshotReader:
    """Inventário somente leitura a partir de snapshot desejado + cache observado."""

    def __init__(self, config_dir: Path, cache_dir: Path | None = None) -> None:
        self._config_dir = config_dir
        self._cache_dir = cache_dir or (config_dir / "cache")

    def read_inventory(self) -> list[InventoryEntry]:
        try:
            config = load_config(self._config_dir)
        except ConfigLoadError:
            return []
        observed_entries = load_observed(self._cache_dir)
        observed_by_provider = {
            entry.get("provider_id"): entry
            for entry in observed_entries
            if isinstance(entry.get("provider_id"), str)
        }
        items: list[InventoryEntry] = []
        providers = config.get("providers", {})
        if isinstance(providers, dict):
            for provider_id, spec in sorted(providers.items()):
                if not isinstance(spec, dict):
                    continue
                observed = observed_by_provider.get(provider_id, {})
                items.append(
                    InventoryEntry(
                        provider_id=provider_id,
                        mode=_optional_str(spec.get("mode")),
                        runtime_class=_optional_str(spec.get("runtime_class")),
                        enabled=_optional_bool(spec.get("enabled")),
                        approved=_optional_bool(spec.get("approved")),
                        default_model=_optional_str(spec.get("default_model")),
                        observed_discovered=bool(observed) if observed else None,
                        observed_healthy=_optional_bool(observed.get("healthy")),
                        availability=CAPABILITY_IMPLEMENTED,
                    )
                )
        functions = config.get("functions", {})
        if isinstance(functions, dict):
            for function_id, spec in sorted(functions.items()):
                if not isinstance(spec, dict):
                    continue
                items.append(
                    InventoryEntry(
                        function_id=function_id,
                        specialist=_optional_str(spec.get("specialist")),
                        version=_optional_str(spec.get("version")),
                        min_status=_optional_str(spec.get("min_status")),
                        availability=CAPABILITY_IMPLEMENTED,
                    )
                )
        return items


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _validate_proposed_manifest(config_dir: Path, manifest: dict[str, Any]) -> bool:
    if manifest.get("schema_version") != CONFIG_SCHEMA_VERSION:
        return False
    parts = manifest.get("parts")
    extras = manifest.get("extras")
    if not isinstance(parts, dict) or set(parts) != {"providers.json", "functions.json"}:
        return False
    if not isinstance(extras, dict):
        return False
    for name, digest in {**parts, **extras}.items():
        if not isinstance(digest, str) or len(digest) != 64:
            return False
        part_path = config_dir / name
        try:
            if part_hash(part_path) != digest:
                return False
        except OSError:
            return False
    return True


@dataclass(frozen=True, slots=True)
class CompositionSources:
    """Fontes opcionais injetadas explicitamente — ausência permanece indisponível."""

    package_version: str
    task_reader: object | None = None
    execution_registry: object | None = None
    clio_emitter: object | None = None
    config_dir: Path | None = None
    cache_dir: Path | None = None
    static_root: Path | None = None
    allowed_origins: frozenset[str] | None = None
    csrf_token: str = ""


def _resolve_task_reader(sources: CompositionSources) -> TaskReader | None:
    candidate = sources.task_reader
    if candidate is None:
        return None
    if isinstance(candidate, TaskReader):
        return candidate
    return None


def compose_dependencies(sources: CompositionSources) -> OlimpoDependencies:
    """Monta dependências reais sem startup automático ou dados inventados."""
    task_reader = _resolve_task_reader(sources)
    execution_reader = (
        RegistryExecutionReader(sources.execution_registry)
        if sources.execution_registry
        else None
    )
    clio_reader = (
        ClioEmitterReader(sources.clio_emitter) if sources.clio_emitter else None
    )
    config_reader = None
    config_validator = None
    config_publisher = None
    inventory_reader = None
    if sources.config_dir is not None:
        config_reader = ConfigDirectoryReader(sources.config_dir)
        config_validator = ConfigDirectoryValidator(sources.config_dir)
        config_publisher = ConfigDirectoryPublisher(sources.config_dir)
        inventory_reader = InventorySnapshotReader(
            sources.config_dir,
            cache_dir=sources.cache_dir,
        )
    static_root = sources.static_root
    allowed_origins = validate_allowed_origins(sources.allowed_origins or frozenset())
    return OlimpoDependencies(
        package_version=sources.package_version,
        task_reader=task_reader,
        execution_reader=execution_reader,
        clio_reader=clio_reader,
        inventory_reader=inventory_reader,
        config_reader=config_reader,
        config_validator=config_validator,
        config_publisher=config_publisher,
        allowed_origins=allowed_origins,
        csrf_token=sources.csrf_token,
        static_root=static_root,
    )
