"""Fronteira Iris local: prepara, verifica, consome e só então delega."""

from __future__ import annotations

import os
import re
import threading
import time
import unicodedata
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

from aegis import DECLARED_OFFLINE, LOCAL_CLI, canonical_digest

from athena.bridge import BridgeRunnerContract, RunRequest, RunResult
from athena.capsule import (
    CapsuleDenied,
    ExecutionCapsule,
    ExecutionPlan,
    issue_capsule,
    verify_capsule,
)
from athena.execution import ExecutionControl, ExecutionRecord
from athena.lease import DirectoryLeaseContract

BASE_ENVIRONMENT_NAMES = (
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "TMPDIR",
    "HOME",
    "USER",
)
_DEFAULT_PATH = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
_DEFAULT_EXECUTION_TIME_BUDGET_S = 300.0
_PORTABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SECRET_MARKERS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "APIKEY",
    "AUTH",
    "AUTHORIZATION",
    "CREDENTIAL",
    "PRIVATEKEY",
    "ACCESSKEY",
)


def _normalized_name(name: str) -> str:
    return unicodedata.normalize("NFKC", name).casefold()


def _secret_like(name: str) -> bool:
    compact = "".join(character for character in name.upper() if character.isalnum())
    return any(marker in compact for marker in _SECRET_MARKERS)


def _minimal_environment(
    requested: object,
    cwd: Path,
    parent: dict[str, str],
) -> MappingProxyType[str, str]:
    if not hasattr(requested, "items"):
        raise CapsuleDenied("ENVIRONMENT_NAME_DENIED")
    base_normalized = {_normalized_name(name) for name in BASE_ENVIRONMENT_NAMES}
    base_normalized.add(_normalized_name("PWD"))
    seen: set[str] = set()
    safe_requested: dict[str, str] = {}
    for name, value in requested.items():  # type: ignore[union-attr]
        if not isinstance(name, str) or not isinstance(value, str):
            raise CapsuleDenied("ENVIRONMENT_NAME_DENIED")
        normalized = _normalized_name(name)
        if (
            _PORTABLE_NAME.fullmatch(name) is None
            or normalized in seen
            or normalized in base_normalized
        ):
            raise CapsuleDenied("ENVIRONMENT_NAME_DENIED")
        if _secret_like(name):
            raise CapsuleDenied("ENVIRONMENT_NAME_SECRET_LIKE")
        seen.add(normalized)
        safe_requested[name] = value

    child = {
        name: parent[name]
        for name in BASE_ENVIRONMENT_NAMES
        if name in parent
    }
    child.setdefault("PATH", _DEFAULT_PATH)
    child.update(safe_requested)
    child["PWD"] = str(cwd)
    return MappingProxyType(dict(sorted(child.items())))


def _plan(
    request: RunRequest,
    execution: ExecutionRecord,
    *,
    fallback_declared: bool,
    tests: tuple[str, ...],
) -> ExecutionPlan:
    cwd = Path(request.cwd).resolve()
    environment = tuple(sorted(dict(request.env).items()))
    return ExecutionPlan(
        schema_version="athena.execution-plan/1",
        contract_version="cap-0/1",
        task_id=f"mcp:{execution.execution_id}",
        execution_id=execution.execution_id,
        attempt_id=execution.attempt_id,
        provider_id=execution.provider,
        access_mode=LOCAL_CLI,
        command=tuple(request.command),
        cwd=str(cwd),
        environment_names=tuple(name for name, _ in environment),
        environment_values_digest=canonical_digest(environment),
        network_policy=DECLARED_OFFLINE,
        resource_scope=(str(cwd),),
        write_scope=(str(cwd),),
        permissions=("execute_local_cli", "read_write_cwd"),
        absolute_timeout_s=execution.absolute_deadline_s,
        idle_timeout_s=execution.idle_deadline_s,
        lease_timeout_s=request.lease_timeout_s,
        termination_grace_s=request.termination_grace_s,
        use_pty=request.use_pty,
        fallback_declared=fallback_declared,
        tests=tests,
        log_level="sanitized",
    )


class LocalIrisBoundary:
    """Adapter local CAP-0 sem seleção ou decisão de fallback."""

    def __init__(
        self,
        runner: BridgeRunnerContract,
        signing_key: bytes,
        *,
        parent_environment: dict[str, str] | None = None,
        network_policy: str = DECLARED_OFFLINE,
        clock: Callable[[], int] | None = None,
    ) -> None:
        self._runner = runner
        self._key = signing_key
        self._parent_environment = (
            dict(os.environ) if parent_environment is None else dict(parent_environment)
        )
        self._network_policy = network_policy
        self._clock = clock or (lambda: int(time.time()))
        self._consumed: set[str] = set()
        self._consume_lock = threading.Lock()

    def prepare_attempt(
        self,
        request: RunRequest,
        execution: ExecutionRecord,
        *,
        fallback_declared: bool,
        tests: tuple[str, ...],
    ) -> RunRequest:
        """Sanitizar o ambiente e obter um selo antes desta tentativa."""
        if self._network_policy != DECLARED_OFFLINE:
            raise CapsuleDenied("UNSUPPORTED_NETWORK_POLICY")
        if execution.absolute_deadline_s is None:
            execution.configure_deadlines(
                absolute_deadline_s=_DEFAULT_EXECUTION_TIME_BUDGET_S,
                idle_deadline_s=execution.idle_deadline_s,
            )
        cwd = Path(request.cwd).resolve()
        environment = _minimal_environment(request.env, cwd, self._parent_environment)
        prepared = replace(
            request,
            command=tuple(request.command),
            cwd=cwd,
            env=environment,
            inherit_environment=False,
            authorization=None,
        )
        plan = _plan(
            prepared,
            execution,
            fallback_declared=fallback_declared,
            tests=tuple(tests),
        )
        capsule = issue_capsule(plan, self._key, now=self._clock())
        return replace(prepared, authorization=capsule)

    def run(
        self,
        request: RunRequest,
        execution: ExecutionRecord,
        lease: DirectoryLeaseContract,
        *,
        control: ExecutionControl | None = None,
    ) -> RunResult:
        """Verificar e consumir imediatamente antes do runner existente."""
        capsule = request.authorization
        if not isinstance(capsule, ExecutionCapsule):
            raise CapsuleDenied("CAPSULE_MISSING")
        environment = MappingProxyType(dict(request.env))
        delegated = replace(
            request,
            command=tuple(request.command),
            env=environment,
            authorization=None,
        )
        expected = _plan(
            delegated,
            execution,
            fallback_declared=capsule.plan.fallback_declared,
            tests=capsule.plan.tests,
        )
        verify_capsule(capsule, expected, self._key, now=self._clock())
        with self._consume_lock:
            if capsule.seal.seal_id in self._consumed:
                raise CapsuleDenied("CAPSULE_CONSUMED")
            self._consumed.add(capsule.seal.seal_id)
        return self._runner.run(delegated, execution, lease, control=control)
