"""Contratos fechados da Cápsula de Execução CAP-0."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from aegis.contracts import ExecutionSeal

_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _closed_string_sequence(value: object) -> tuple[str, ...]:
    if (
        isinstance(value, (str, bytes, bytearray, Mapping))
        or not isinstance(value, Sequence)
    ):
        raise TypeError("CAPSULE_INVALID")
    normalized = tuple(value)
    if any(not isinstance(item, str) or not item for item in normalized):
        raise ValueError("CAPSULE_INVALID")
    return normalized


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """Snapshot sem valores de ambiente do plano exato de uma tentativa."""

    schema_version: str
    contract_version: str
    task_id: str
    execution_id: str
    attempt_id: str
    provider_id: str
    access_mode: str
    command: tuple[str, ...]
    cwd: str
    environment_names: tuple[str, ...]
    environment_values_digest: str
    network_policy: str
    resource_scope: tuple[str, ...]
    write_scope: tuple[str, ...]
    permissions: tuple[str, ...]
    absolute_timeout_s: float | None
    idle_timeout_s: float | None
    lease_timeout_s: float | None
    termination_grace_s: float
    use_pty: bool
    fallback_declared: bool
    tests: tuple[str, ...]
    log_level: str

    def __post_init__(self) -> None:
        for field in (
            "command",
            "environment_names",
            "resource_scope",
            "write_scope",
            "permissions",
            "tests",
        ):
            object.__setattr__(self, field, _closed_string_sequence(getattr(self, field)))
        if not self.command:
            raise ValueError("CAPSULE_INVALID")
        string_values = (
            self.schema_version,
            self.contract_version,
            self.task_id,
            self.execution_id,
            self.attempt_id,
            self.provider_id,
            self.access_mode,
            self.cwd,
            self.environment_values_digest,
            self.network_policy,
            self.log_level,
        )
        if any(not isinstance(value, str) or not value for value in string_values):
            raise ValueError("CAPSULE_INVALID")
        if _DIGEST_PATTERN.fullmatch(self.environment_values_digest) is None:
            raise ValueError("CAPSULE_INVALID")
        for value in (
            self.absolute_timeout_s,
            self.idle_timeout_s,
            self.lease_timeout_s,
        ):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value <= 0
                or not math.isfinite(value)
            ):
                raise ValueError("CAPSULE_INVALID")
        if (
            isinstance(self.termination_grace_s, bool)
            or not isinstance(self.termination_grace_s, (int, float))
            or self.termination_grace_s < 0
            or not math.isfinite(self.termination_grace_s)
        ):
            raise ValueError("CAPSULE_INVALID")
        if not isinstance(self.use_pty, bool) or not isinstance(
            self.fallback_declared, bool
        ):
            raise TypeError("CAPSULE_INVALID")


@dataclass(frozen=True, slots=True)
class ExecutionCapsule:
    """Plano imutável e seu Selo Aegis de tentativa única."""

    plan: ExecutionPlan
    plan_digest: str
    seal: ExecutionSeal

    def __post_init__(self) -> None:
        if not isinstance(self.plan, ExecutionPlan) or not isinstance(self.seal, ExecutionSeal):
            raise TypeError("CAPSULE_INVALID")
        if not isinstance(self.plan_digest, str) or len(self.plan_digest) != 64:
            raise ValueError("CAPSULE_INVALID")


class CapsuleDenied(RuntimeError):
    """Negação terminal sanitizada antes do bridge."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)
