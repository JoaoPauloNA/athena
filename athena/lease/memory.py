"""Implementação em memória de leases de diretório."""

from __future__ import annotations

import math
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .contracts import LeaseAcquisitionTimeout, LeaseOwnershipError


@dataclass(frozen=True, slots=True)
class _LeaseOwner:
    execution_id: str
    attempt_id: str


def _validate_identifier(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _validate_timeout(timeout: float | None) -> float | None:
    if timeout is None:
        return None
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise TypeError("timeout must be a non-negative finite number or None")
    value = float(timeout)
    if value < 0 or not math.isfinite(value):
        raise ValueError("timeout must be a non-negative finite number or None")
    return value


class DirectoryLeaseManager:
    """Gerenciador thread-safe de leases canônicos dentro de um processo."""

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.Lock())
        self._owners: dict[Path, _LeaseOwner] = {}

    def canonicalize(self, directory: str | Path) -> Path:
        """Resolver uma chave absoluta, normalizada e sem links simbólicos."""
        if not isinstance(directory, (str, os.PathLike)):
            raise TypeError("directory must be a path-like value")
        if isinstance(directory, str) and not directory:
            raise ValueError("directory must not be empty")
        return Path(os.path.realpath(os.fspath(directory)))

    def acquire(
        self,
        directory: str | Path,
        execution_id: str,
        attempt_id: str,
        *,
        timeout: float | None = None,
    ) -> Path:
        """Adquirir um diretório, esperando no máximo ``timeout`` segundos."""
        key = self.canonicalize(directory)
        owner = _LeaseOwner(
            _validate_identifier("execution_id", execution_id),
            _validate_identifier("attempt_id", attempt_id),
        )
        wait_limit = _validate_timeout(timeout)
        deadline = None if wait_limit is None else time.monotonic() + wait_limit

        with self._condition:
            while True:
                current = self._owners.get(key)
                if current is None:
                    self._owners[key] = owner
                    return key
                if current == owner:
                    return key

                remaining = (
                    None if deadline is None else max(0.0, deadline - time.monotonic())
                )
                if remaining == 0.0:
                    raise LeaseAcquisitionTimeout(
                        f"timed out acquiring lease for directory: {key}"
                    )
                self._condition.wait(timeout=remaining)

    def transfer(
        self,
        directory: str | Path,
        execution_id: str,
        current_attempt_id: str,
        next_attempt_id: str,
    ) -> Path:
        """Trocar o proprietário sem liberar o diretório a concorrentes."""
        key = self.canonicalize(directory)
        current_owner = _LeaseOwner(
            _validate_identifier("execution_id", execution_id),
            _validate_identifier("current_attempt_id", current_attempt_id),
        )
        next_owner = _LeaseOwner(
            current_owner.execution_id,
            _validate_identifier("next_attempt_id", next_attempt_id),
        )

        with self._condition:
            actual_owner = self._owners.get(key)
            if actual_owner != current_owner:
                raise LeaseOwnershipError(
                    f"lease is not owned by the requested attempt: {key}"
                )
            self._owners[key] = next_owner
        return key

    def release(
        self,
        directory: str | Path,
        execution_id: str,
        attempt_id: str,
    ) -> None:
        """Liberar o lease e despertar aquisições concorrentes."""
        key = self.canonicalize(directory)
        owner = _LeaseOwner(
            _validate_identifier("execution_id", execution_id),
            _validate_identifier("attempt_id", attempt_id),
        )

        with self._condition:
            if self._owners.get(key) != owner:
                raise LeaseOwnershipError(
                    f"lease is not owned by the requested attempt: {key}"
                )
            del self._owners[key]
            self._condition.notify_all()


_default_manager = DirectoryLeaseManager()


def canonicalize_workspace(directory: str | Path) -> Path:
    """Canonicalizar um diretório usando a instância padrão do processo."""
    return _default_manager.canonicalize(directory)


def acquire(
    directory: str | Path,
    execution_id: str,
    attempt_id: str,
    *,
    timeout: float | None = None,
) -> Path:
    """Adquirir um lease na instância padrão do processo."""
    return _default_manager.acquire(
        directory,
        execution_id,
        attempt_id,
        timeout=timeout,
    )


def transfer(
    directory: str | Path,
    execution_id: str,
    current_attempt_id: str,
    next_attempt_id: str,
) -> Path:
    """Transferir um lease na instância padrão do processo."""
    return _default_manager.transfer(
        directory,
        execution_id,
        current_attempt_id,
        next_attempt_id,
    )


def release(directory: str | Path, execution_id: str, attempt_id: str) -> None:
    """Liberar um lease na instância padrão do processo."""
    _default_manager.release(directory, execution_id, attempt_id)
