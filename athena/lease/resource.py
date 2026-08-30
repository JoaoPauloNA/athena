"""Leases granulares multi-recurso com reserva atômica e FIFO limitada."""

from __future__ import annotations

import math
import os
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .contracts import LeaseAcquisitionTimeout, LeaseOwnershipError

MAX_OWNER_FIELD_LEN = 128
MAX_RESOURCE_KEYS = 64
MAX_WAIT_QUEUE = 256
DEFAULT_TTL_S = 30.0
MIN_TTL_S = 0.1
MAX_TTL_S = 300.0
DEFAULT_ACQUIRE_TIMEOUT_S = 30.0
GLOBAL_RESOURCE_PREFIX = "__global__:"
WORKSPACE_GLOBAL_KEY = f"{GLOBAL_RESOURCE_PREFIX}workspace"


class AccessMode(str, Enum):
    READ = "read"
    WRITE = "write"
    GLOBAL = "global"


@dataclass(frozen=True, slots=True, order=True)
class ResourceOwner:
    task_id: str
    subtask_id: str
    attempt_id: str


@dataclass(frozen=True, slots=True)
class ResourceRequest:
    key: str
    mode: AccessMode
    path: Path | None = None


@dataclass(frozen=True, slots=True)
class BusyEstimate:
    waiters_ahead: int
    estimated_wait_s: float


def _validate_identifier(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    if len(value) > MAX_OWNER_FIELD_LEN:
        raise ValueError(f"{name} exceeds maximum length")
    return value


def _validate_timeout(timeout: float | None) -> float:
    if timeout is None:
        return DEFAULT_ACQUIRE_TIMEOUT_S
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise TypeError("timeout must be a non-negative finite number or None")
    value = float(timeout)
    if value < 0 or not math.isfinite(value):
        raise ValueError("timeout must be a non-negative finite number or None")
    return value


def _validate_ttl(ttl_s: float) -> float:
    if isinstance(ttl_s, bool) or not isinstance(ttl_s, (int, float)):
        raise TypeError("ttl_s must be a finite number")
    value = float(ttl_s)
    if not MIN_TTL_S <= value <= MAX_TTL_S:
        raise ValueError("ttl_s out of bounds")
    return value


def _paths_overlap(left: Path, right: Path) -> bool:
    """True when paths are equal or one is an ancestor of the other."""
    if left == right:
        return True
    try:
        left.relative_to(right)
        return True
    except ValueError:
        pass
    try:
        right.relative_to(left)
        return True
    except ValueError:
        return False


def _write_conflicts_with_held(
    request_path: Path,
    held_path: Path,
    held_mode: AccessMode,
    *,
    held_owner: ResourceOwner,
    request_owner: ResourceOwner,
    read_holders: frozenset[ResourceOwner],
) -> bool:
    if not _paths_overlap(request_path, held_path):
        return False
    if held_mode == AccessMode.WRITE:
        return held_owner != request_owner
    if held_mode == AccessMode.READ:
        return bool(read_holders - {request_owner})
    return False


def _read_conflicts_with_held(
    request_path: Path,
    held_path: Path,
    held_mode: AccessMode,
    *,
    held_owner: ResourceOwner,
    request_owner: ResourceOwner,
    read_holders: frozenset[ResourceOwner],
) -> bool:
    if not _paths_overlap(request_path, held_path):
        return False
    if held_mode == AccessMode.WRITE:
        return held_owner != request_owner
    if held_mode == AccessMode.READ:
        return False
    return False


@dataclass(slots=True)
class _HeldResource:
    owner: ResourceOwner
    mode: AccessMode
    path: Path | None
    read_expires: dict[ResourceOwner, float]
    write_expires_at: float | None = None

    @property
    def read_holders(self) -> frozenset[ResourceOwner]:
        return frozenset(self.read_expires)


class ResourceLeaseManager:
    """Reserva atômica multi-recurso com leitura compartilhada e escrita exclusiva."""

    def __init__(self, *, default_ttl_s: float = DEFAULT_TTL_S) -> None:
        self._default_ttl_s = _validate_ttl(default_ttl_s)
        self._condition = threading.Condition(threading.Lock())
        self._held: dict[str, _HeldResource] = {}
        self._waiters: list[int] = []
        self._waiter_seq = 0

    def canonicalize_path(self, path: str | Path) -> Path:
        if not isinstance(path, (str, os.PathLike)):
            raise TypeError("path must be a path-like value")
        text = os.fspath(path)
        if not text or text != os.path.normpath(text):
            raise ValueError("path must be normalized")
        if ".." in Path(text).parts:
            raise ValueError("path must not traverse upward")
        resolved = Path(os.path.realpath(text))
        return resolved

    def resource_key_for_path(self, path: str | Path, *, mode: AccessMode) -> str:
        canonical = self.canonicalize_path(path)
        if mode == AccessMode.GLOBAL:
            raise ValueError("global resources require resource_key_for_global")
        prefix = "file:" if mode == AccessMode.WRITE else "read:"
        return f"{prefix}{canonical}"

    def resource_key_for_global(self, name: str = "workspace") -> str:
        normalized = _validate_identifier("global_name", name)
        if normalized != "workspace":
            raise ValueError("only workspace-global resource is supported")
        return WORKSPACE_GLOBAL_KEY

    def _purge_expired_locked(self, now: float) -> None:
        expired_keys: list[str] = []
        for key, held in self._held.items():
            if held.mode == AccessMode.READ:
                active = {
                    owner: expires
                    for owner, expires in held.read_expires.items()
                    if expires > now
                }
                if not active:
                    expired_keys.append(key)
                elif active != held.read_expires:
                    self._held[key] = _HeldResource(
                        owner=held.owner,
                        mode=AccessMode.READ,
                        path=held.path,
                        read_expires=active,
                    )
            elif held.write_expires_at is not None and held.write_expires_at <= now:
                expired_keys.append(key)
        for key in expired_keys:
            del self._held[key]
        if expired_keys:
            self._condition.notify_all()

    def _validate_request(
        self,
        request: ResourceRequest,
        *,
        seen_keys: set[str],
    ) -> None:
        if request.key in seen_keys:
            raise ValueError(f"duplicate resource key in bundle: {request.key}")
        seen_keys.add(request.key)
        if request.mode == AccessMode.GLOBAL:
            expected = self.resource_key_for_global()
            if request.key != expected:
                raise ValueError("global resource key mismatch")
            if request.path is not None:
                raise ValueError("global resource must not carry path")
            return
        if request.path is None:
            raise ValueError("path resource requires canonical path")
        canonical = self.canonicalize_path(request.path)
        expected_key = self.resource_key_for_path(canonical, mode=request.mode)
        if request.key != expected_key:
            raise ValueError("resource key does not match canonical path/mode")
        if Path(os.path.realpath(request.path)) != canonical:
            raise ValueError("resource path is not canonical")

    def _global_conflicts(self, owner: ResourceOwner) -> bool:
        for held in self._held.values():
            if held.owner != owner:
                return True
        return False

    def _conflicts(
        self,
        request: ResourceRequest,
        owner: ResourceOwner,
        *,
        now: float,
    ) -> bool:
        if request.mode == AccessMode.GLOBAL:
            return self._global_conflicts(owner)

        if request.path is None:
            held = self._held.get(request.key)
            if held is None:
                return False
            if held.mode == AccessMode.WRITE:
                return held.owner != owner
            if request.mode == AccessMode.WRITE:
                return held.owner != owner or bool(held.read_holders - {owner})
            return False

        request_path = request.path
        for held in self._held.values():
            if held.mode == AccessMode.GLOBAL:
                if held.owner != owner:
                    return True
                continue
            if held.path is None:
                continue
            if request.mode == AccessMode.WRITE:
                if _write_conflicts_with_held(
                    request_path,
                    held.path,
                    held.mode,
                    held_owner=held.owner,
                    request_owner=owner,
                    read_holders=held.read_holders,
                ):
                    return True
            elif request.mode == AccessMode.READ and _read_conflicts_with_held(
                request_path,
                held.path,
                held.mode,
                held_owner=held.owner,
                request_owner=owner,
                read_holders=held.read_holders,
            ):
                return True
        return False

    def _try_acquire_one_locked(
        self,
        request: ResourceRequest,
        owner: ResourceOwner,
        *,
        now: float,
        ttl_s: float,
    ) -> bool:
        if self._conflicts(request, owner, now=now):
            return False
        expires_at = now + ttl_s

        if request.mode == AccessMode.READ:
            held = self._held.get(request.key)
            if held is None:
                self._held[request.key] = _HeldResource(
                    owner=owner,
                    mode=AccessMode.READ,
                    path=request.path,
                    read_expires={owner: expires_at},
                )
            else:
                updated = dict(held.read_expires)
                updated[owner] = expires_at
                self._held[request.key] = _HeldResource(
                    owner=held.owner,
                    mode=AccessMode.READ,
                    path=held.path,
                    read_expires=updated,
                )
            return True

        self._held[request.key] = _HeldResource(
            owner=owner,
            mode=request.mode,
            path=request.path,
            read_expires={},
            write_expires_at=expires_at,
        )
        return True

    def _release_one_locked(self, request: ResourceRequest, owner: ResourceOwner) -> None:
        held = self._held.get(request.key)
        if held is None:
            return
        if request.mode == AccessMode.READ:
            if owner not in held.read_holders:
                return
            remaining = {
                o: exp for o, exp in held.read_expires.items() if o != owner
            }
            if remaining:
                self._held[request.key] = _HeldResource(
                    owner=held.owner,
                    mode=AccessMode.READ,
                    path=held.path,
                    read_expires=remaining,
                )
            else:
                del self._held[request.key]
            return
        if held.owner != owner:
            raise LeaseOwnershipError(f"foreign owner cannot release: {request.key}")
        del self._held[request.key]

    def acquire_all(
        self,
        requests: tuple[ResourceRequest, ...],
        owner: ResourceOwner,
        *,
        timeout: float | None = None,
        ttl_s: float | None = None,
    ) -> tuple[str, ...]:
        if len(requests) > MAX_RESOURCE_KEYS:
            raise ValueError("too many resource keys")
        validated_owner = ResourceOwner(
            _validate_identifier("task_id", owner.task_id),
            _validate_identifier("subtask_id", owner.subtask_id),
            _validate_identifier("attempt_id", owner.attempt_id),
        )
        ordered = tuple(sorted(requests, key=lambda item: item.key))
        wait_limit = _validate_timeout(timeout)
        ttl = _validate_ttl(self._default_ttl_s if ttl_s is None else ttl_s)
        deadline = time.monotonic() + wait_limit
        seen_keys: set[str] = set()
        for request in ordered:
            self._validate_request(request, seen_keys=seen_keys)

        with self._condition:
            if len(self._waiters) >= MAX_WAIT_QUEUE:
                raise LeaseAcquisitionTimeout("resource wait queue is full")

            ticket = self._waiter_seq
            self._waiter_seq += 1
            self._waiters.append(ticket)

            try:
                while True:
                    now = time.monotonic()
                    self._purge_expired_locked(now)

                    ahead = [item for item in self._waiters if item < ticket]
                    if ahead:
                        remaining = max(0.0, deadline - time.monotonic())
                        if remaining == 0.0:
                            raise LeaseAcquisitionTimeout(
                                "timed out waiting in FIFO queue"
                            )
                        self._condition.wait(timeout=remaining)
                        continue

                    acquired: list[str] = []
                    failed = False
                    for request in ordered:
                        if self._try_acquire_one_locked(
                            request, validated_owner, now=now, ttl_s=ttl
                        ):
                            acquired.append(request.key)
                        else:
                            failed = True
                            break

                    if not failed:
                        return tuple(acquired)

                    for key in reversed(acquired):
                        matching = next(
                            request for request in ordered if request.key == key
                        )
                        self._release_one_locked(matching, validated_owner)

                    remaining = max(0.0, deadline - time.monotonic())
                    if remaining == 0.0:
                        raise LeaseAcquisitionTimeout(
                            "timed out acquiring resource bundle"
                        )
                    self._condition.wait(timeout=remaining)
            finally:
                self._waiters = [
                    item for item in self._waiters if item != ticket
                ]
                self._condition.notify_all()

    def _validate_ownership_locked(
        self,
        requests: tuple[ResourceRequest, ...],
        owner: ResourceOwner,
    ) -> None:
        for request in requests:
            held = self._held.get(request.key)
            if held is None:
                raise LeaseOwnershipError(
                    f"resource not held: {request.key}"
                )
            if request.mode == AccessMode.READ:
                if owner not in held.read_holders:
                    raise LeaseOwnershipError(
                        f"foreign owner cannot access: {request.key}"
                    )
            elif held.owner != owner:
                raise LeaseOwnershipError(
                    f"foreign owner cannot access: {request.key}"
                )

    def heartbeat(
        self,
        requests: tuple[ResourceRequest, ...],
        owner: ResourceOwner,
        *,
        ttl_s: float | None = None,
    ) -> None:
        validated_owner = ResourceOwner(
            _validate_identifier("task_id", owner.task_id),
            _validate_identifier("subtask_id", owner.subtask_id),
            _validate_identifier("attempt_id", owner.attempt_id),
        )
        ttl = _validate_ttl(self._default_ttl_s if ttl_s is None else ttl_s)
        ordered = tuple(sorted(requests, key=lambda item: item.key))
        now = time.monotonic()
        with self._condition:
            self._purge_expired_locked(now)
            self._validate_ownership_locked(ordered, validated_owner)
            expires_at = now + ttl
            for request in ordered:
                held = self._held[request.key]
                if request.mode == AccessMode.READ:
                    updated = dict(held.read_expires)
                    updated[validated_owner] = expires_at
                    self._held[request.key] = _HeldResource(
                        owner=held.owner,
                        mode=AccessMode.READ,
                        path=held.path,
                        read_expires=updated,
                    )
                else:
                    self._held[request.key] = _HeldResource(
                        owner=held.owner,
                        mode=held.mode,
                        path=held.path,
                        read_expires=held.read_expires,
                        write_expires_at=expires_at,
                    )

    def release_all(
        self,
        requests: tuple[ResourceRequest, ...],
        owner: ResourceOwner,
    ) -> None:
        validated_owner = ResourceOwner(
            _validate_identifier("task_id", owner.task_id),
            _validate_identifier("subtask_id", owner.subtask_id),
            _validate_identifier("attempt_id", owner.attempt_id),
        )
        ordered = tuple(sorted(requests, key=lambda item: item.key, reverse=True))
        with self._condition:
            for request in ordered:
                held = self._held.get(request.key)
                if held is None:
                    continue
                if request.mode == AccessMode.READ:
                    if validated_owner not in held.read_holders:
                        continue
                elif held.owner != validated_owner:
                    raise LeaseOwnershipError(
                        f"foreign owner cannot release: {request.key}"
                    )
            for request in ordered:
                self._release_one_locked(request, validated_owner)
            self._condition.notify_all()

    def busy_estimate(self) -> BusyEstimate:
        with self._condition:
            waiters = len(self._waiters)
            return BusyEstimate(
                waiters_ahead=waiters,
                estimated_wait_s=min(1.0 + 0.05 * waiters, 30.0),
            )
