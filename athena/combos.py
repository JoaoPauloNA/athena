"""Gerenciamento de combos do Athena-MCP.

Um combo é uma sequência ordenada de providers com política de failover.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from athena.config import COMBOS_FILE

_LOCK = threading.Lock()


@dataclass
class ComboStep:
    provider_id: str
    model: Optional[str] = None
    role: Optional[str] = None
    timeout: Optional[int] = None
    skip_permissions: bool = False
    extra_args: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d: dict = {"provider_id": self.provider_id}
        if self.model is not None:
            d["model"] = self.model
        if self.role is not None:
            d["role"] = self.role
        if self.timeout is not None:
            d["timeout"] = self.timeout
        if self.skip_permissions:
            d["skip_permissions"] = True
        if self.extra_args:
            d["extra_args"] = self.extra_args
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "ComboStep":
        return cls(
            provider_id=data["provider_id"],
            model=data.get("model"),
            role=data.get("role"),
            timeout=data.get("timeout"),
            skip_permissions=bool(data.get("skip_permissions", False)),
            extra_args=list(data.get("extra_args", [])),
        )


@dataclass
class FailoverPolicy:
    on_timeout: bool = True
    on_error: bool = True
    on_quota_exceeded: bool = True
    max_retries_per_provider: int = 1
    retry_delay_seconds: int = 2

    def to_dict(self) -> dict:
        return {
            "on_timeout": self.on_timeout,
            "on_error": self.on_error,
            "on_quota_exceeded": self.on_quota_exceeded,
            "max_retries_per_provider": self.max_retries_per_provider,
            "retry_delay_seconds": self.retry_delay_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FailoverPolicy":
        return cls(
            on_timeout=bool(data.get("on_timeout", True)),
            on_error=bool(data.get("on_error", True)),
            on_quota_exceeded=bool(data.get("on_quota_exceeded", True)),
            max_retries_per_provider=int(data.get("max_retries_per_provider", 1)),
            retry_delay_seconds=int(data.get("retry_delay_seconds", 2)),
        )


@dataclass
class Combo:
    id: str
    name: str
    description: str = ""
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""
    chain: List[ComboStep] = field(default_factory=list)
    failover_policy: FailoverPolicy = field(default_factory=FailoverPolicy)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "chain": [step.to_dict() for step in self.chain],
            "failover_policy": self.failover_policy.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Combo":
        return cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            enabled=bool(data.get("enabled", True)),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            chain=[ComboStep.from_dict(s) for s in data.get("chain", [])],
            failover_policy=FailoverPolicy.from_dict(data.get("failover_policy", {})),
        )


def _load_combos() -> Dict[str, Combo]:
    if not COMBOS_FILE.exists():
        return {}
    try:
        data = json.loads(COMBOS_FILE.read_text(encoding="utf-8"))
        return {c["id"]: Combo.from_dict(c) for c in data.get("combos", [])}
    except (json.JSONDecodeError, OSError, KeyError):
        return {}


def _save_combos(combos: Dict[str, Combo]) -> None:
    COMBOS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "1.0",
        "combos": [c.to_dict() for c in combos.values()],
    }
    COMBOS_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def list_combos() -> List[Combo]:
    with _LOCK:
        return list(_load_combos().values())


def get_combo(combo_id: str) -> Optional[Combo]:
    with _LOCK:
        return _load_combos().get(combo_id)


def create_combo(
    combo_id: str,
    name: str,
    chain: List[ComboStep],
    *,
    description: str = "",
    failover_policy: Optional[FailoverPolicy] = None,
) -> Combo:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    combo = Combo(
        id=combo_id,
        name=name,
        description=description,
        enabled=True,
        created_at=now,
        updated_at=now,
        chain=chain,
        failover_policy=failover_policy or FailoverPolicy(),
    )
    with _LOCK:
        combos = _load_combos()
        combos[combo_id] = combo
        _save_combos(combos)
    return combo


def update_combo(combo_id: str, **kwargs) -> Optional[Combo]:
    with _LOCK:
        combos = _load_combos()
        combo = combos.get(combo_id)
        if combo is None:
            return None
        for key, value in kwargs.items():
            if hasattr(combo, key):
                setattr(combo, key, value)
        combo.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _save_combos(combos)
        return combo


def delete_combo(combo_id: str) -> bool:
    with _LOCK:
        combos = _load_combos()
        if combo_id not in combos:
            return False
        del combos[combo_id]
        _save_combos(combos)
        return True


def ensure_default_combo() -> Combo:
    """Cria o combo padrão 'default' se não existir."""
    with _LOCK:
        combos = _load_combos()
        if "default" in combos:
            return combos["default"]

    # Combo padrão: tenta agent -> agy -> claude
    from athena.providers import PROVIDER_IDS

    chain = []
    for pid in ("agent", "agy", "claude"):
        if pid in PROVIDER_IDS:
            chain.append(ComboStep(provider_id=pid))

    return create_combo(
        combo_id="default",
        name="Padrão",
        description="Combo padrão com failover entre providers disponíveis",
        chain=chain,
    )
