"""Pool limitado de tokens CPU/RAM/GPU/provider com backpressure."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass

from .contracts import (
    MAX_CPU_TOKENS,
    MAX_GPU_TOKENS,
    MAX_PROVIDER_TOKENS,
    MAX_RAM_MB,
    REASON_BUSY,
    ResourceBudget,
)


def _validate_limit(value: object, *, name: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if not 0 <= value <= maximum:
        raise ValueError(f"{name} out of bounds")
    return value


@dataclass(frozen=True, slots=True)
class TokenPoolLimits:
    cpu_tokens: int = MAX_CPU_TOKENS
    ram_mb: int = MAX_RAM_MB
    gpu_tokens: int = MAX_GPU_TOKENS
    provider_tokens: int = MAX_PROVIDER_TOKENS

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "cpu_tokens", _validate_limit(self.cpu_tokens, name="cpu_tokens", maximum=MAX_CPU_TOKENS)
        )
        object.__setattr__(
            self, "ram_mb", _validate_limit(self.ram_mb, name="ram_mb", maximum=MAX_RAM_MB)
        )
        object.__setattr__(
            self,
            "gpu_tokens",
            _validate_limit(self.gpu_tokens, name="gpu_tokens", maximum=MAX_GPU_TOKENS),
        )
        object.__setattr__(
            self,
            "provider_tokens",
            _validate_limit(
                self.provider_tokens, name="provider_tokens", maximum=MAX_PROVIDER_TOKENS
            ),
        )


@dataclass(frozen=True, slots=True)
class TokenReservation:
    reservation_id: str
    cpu_tokens: int
    ram_mb: int
    gpu_tokens: int
    provider_tokens: int


class ResourceTokenPool:
    """Reserva atômica de tokens com liberação idempotente."""

    def __init__(self, limits: TokenPoolLimits | None = None) -> None:
        configured = limits or TokenPoolLimits()
        self._cpu_total = configured.cpu_tokens
        self._ram_total = configured.ram_mb
        self._gpu_total = configured.gpu_tokens
        self._provider_total = configured.provider_tokens
        self._lock = threading.Lock()
        self._cpu_available = self._cpu_total
        self._ram_available = self._ram_total
        self._gpu_available = self._gpu_total
        self._provider_available = self._provider_total
        self._active: set[str] = set()
        self._limits = configured

    @property
    def limits(self) -> TokenPoolLimits:
        return self._limits

    def try_acquire(self, budget: ResourceBudget) -> TokenReservation | None:
        with self._lock:
            if (
                budget.cpu_tokens <= self._cpu_available
                and budget.ram_mb <= self._ram_available
                and budget.gpu_tokens <= self._gpu_available
                and budget.provider_tokens <= self._provider_available
            ):
                self._cpu_available -= budget.cpu_tokens
                self._ram_available -= budget.ram_mb
                self._gpu_available -= budget.gpu_tokens
                self._provider_available -= budget.provider_tokens
                reservation_id = uuid.uuid4().hex
                self._active.add(reservation_id)
                return TokenReservation(
                    reservation_id,
                    budget.cpu_tokens,
                    budget.ram_mb,
                    budget.gpu_tokens,
                    budget.provider_tokens,
                )
        return None

    def release(self, reservation: TokenReservation | None) -> None:
        if reservation is None:
            return
        with self._lock:
            if reservation.reservation_id not in self._active:
                return
            self._active.discard(reservation.reservation_id)
            self._cpu_available = min(
                self._cpu_total, self._cpu_available + reservation.cpu_tokens
            )
            self._ram_available = min(
                self._ram_total, self._ram_available + reservation.ram_mb
            )
            self._gpu_available = min(
                self._gpu_total, self._gpu_available + reservation.gpu_tokens
            )
            self._provider_available = min(
                self._provider_total,
                self._provider_available + reservation.provider_tokens,
            )


def budget_busy_reason() -> str:
    return REASON_BUSY
