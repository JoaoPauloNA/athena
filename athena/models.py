from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from athena.bridge import which
from athena.config import MODELS_CATALOG_FILE, MODELS_TTL_DAYS

_LOCK = threading.Lock()
_SESSION_ENSURED = False

# Fallback estático (IDs reais das CLIs) quando discovery falha / CLI offline.
FALLBACK_CATALOG: Dict[str, List[Dict[str, str]]] = {
    "agent": [
        {"id": "auto", "name": "Auto (default)", "weight": "light"},
        {"id": "composer-2.5", "name": "Composer 2.5", "weight": "light"},
        {"id": "composer-2.5-fast", "name": "Composer 2.5 Fast", "weight": "light"},
        {"id": "claude-sonnet-5-medium", "name": "Sonnet 5 Medium", "weight": "medium"},
        {"id": "gpt-5.5-medium", "name": "GPT-5.5", "weight": "medium"},
        {"id": "cursor-grok-4.5-high", "name": "Cursor Grok 4.5", "weight": "heavy"},
        {"id": "claude-opus-4-8-thinking-high", "name": "Opus 4.8 Thinking", "weight": "heavy"},
        {"id": "claude-fable-5-thinking-high", "name": "Fable 5 Thinking", "weight": "heavy"},
    ],
    "agy": [
        {"id": "gemini-3.6-flash-medium", "name": "Gemini 3.6 Flash (Medium)", "weight": "light"},
        {"id": "gemini-3.5-flash-medium", "name": "Gemini 3.5 Flash (Medium)", "weight": "light"},
        {"id": "gemini-3.5-flash-high", "name": "Gemini 3.5 Flash (High)", "weight": "medium"},
        {"id": "gpt-oss-120b-medium", "name": "GPT-OSS 120B (Medium)", "weight": "medium"},
        {"id": "gemini-3.1-pro-high", "name": "Gemini 3.1 Pro (High)", "weight": "heavy"},
        {"id": "claude-opus-4-6-thinking", "name": "Claude Opus 4.6 (Thinking)", "weight": "heavy"},
    ],
    "claude": [
        {"id": "haiku", "name": "Haiku", "weight": "light"},
        {"id": "sonnet", "name": "Sonnet", "weight": "medium"},
        {"id": "opus", "name": "Opus", "weight": "heavy"},
    ],
    "codex": [
        {"id": "gpt-5.5", "name": "GPT-5.5", "weight": "medium"},
        {"id": "gpt-5.2", "name": "GPT-5.2", "weight": "medium"},
        {"id": "o4-mini", "name": "o4-mini", "weight": "light"},
    ],
    "openclaude": [
        {"id": "haiku", "name": "Haiku (alias)", "weight": "light"},
        {"id": "sonnet", "name": "Sonnet (alias)", "weight": "medium"},
        {"id": "opus", "name": "Opus (alias)", "weight": "heavy"},
    ],
    "opencode": [
        {"id": "opencode/deepseek-v4-flash-free", "name": "DeepSeek V4 Flash (grátis)", "weight": "light"},
        {"id": "opencode/nemotron-3-ultra-free", "name": "Nemotron 3 Ultra (grátis)", "weight": "light"},
        {"id": "opencode/ling-3.0-flash-free", "name": "Ling 3.0 Flash (grátis)", "weight": "light"},
        {"id": "opencode/longcat-2.0-free", "name": "LongCat 2.0 (grátis)", "weight": "light"},
        {"id": "opencode/mimo-v2.5-free", "name": "MiMo v2.5 (grátis)", "weight": "light"},
        {"id": "opencode/north-mini-code-free", "name": "North Mini Code (grátis)", "weight": "light"},
        {"id": "opencode/big-pickle", "name": "Big Pickle", "weight": "medium"},
    ],
}

FALLBACK_DEFAULTS: Dict[str, str] = {
    "agy": "gemini-3.6-flash-medium",
    "claude": "sonnet",
    "codex": "gpt-5.5",
    "openclaude": "sonnet",
}

_AGENT_LINE = re.compile(r"^([a-zA-Z0-9._\[\]=-]+)\s+-\s+(.+)$")
_AGY_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$", re.I)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str) -> Optional[float]:
    try:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).timestamp()
    except (TypeError, ValueError):
        return None


def classify_weight(model_id: str, display_name: str = "") -> str:
    key = f"{model_id} {display_name}".lower()
    heavy_hints = (
        "opus", "fable", "grok-4.5-high", "pro-high",
        "thinking-high", "thinking-xhigh", "thinking-max",
        "xhigh", "extra high", "max",
    )
    light_hints = (
        "auto", "composer", "flash-low", "flash-medium",
        "flash-minimal", "haiku", "mini", "nano", "low", "none",
    )
    if any(h in key for h in heavy_hints) and "flash" not in key:
        return "heavy"
    if any(h in key for h in light_hints):
        return "light"
    return "medium"


def _pick_recommended(provider_id: str, models: List[Dict[str, str]]) -> Optional[str]:
    if not models:
        return FALLBACK_DEFAULTS.get(provider_id)
    if provider_id == "agent":
        for preferred in ("composer-2.5", "composer-2.5-fast", "auto"):
            if any(m["id"] == preferred for m in models):
                return preferred
    if provider_id == "agy":
        for preferred in (
            "gemini-3.6-flash-medium", "gemini-3.5-flash-medium",
            "gemini-3.6-flash-low", "gemini-3.5-flash-low",
        ):
            if any(m["id"] == preferred for m in models):
                return preferred
    if provider_id == "codex":
        for preferred in ("gpt-5.5", "gpt-5.2", "o4-mini"):
            if any(m["id"] == preferred for m in models):
                return preferred
    if provider_id == "claude":
        for preferred in ("sonnet", "haiku", "opus"):
            if any(m["id"] == preferred for m in models):
                return preferred
    if provider_id == "openclaude":
        for preferred in ("sonnet", "haiku", "opus"):
            if any(m["id"] == preferred for m in models):
                return preferred
    lights = [m["id"] for m in models if m.get("weight") == "light"]
    if lights:
        for mid in lights:
            if mid != "auto" or provider_id == "agent":
                return mid
    return models[0]["id"]


def _run_cli(argv: List[str], *, timeout: int = 20) -> Tuple[int, str]:
    import subprocess

    try:
        completed = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=os.environ.copy(),
        )
        out = (completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else "")
        return completed.returncode, out
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def _discover_agent(binary: str) -> List[Dict[str, str]]:
    code, out = _run_cli([binary, "--list-models"])
    if code != 0 and "Available models" not in out:
        code, out = _run_cli([binary, "models"])
    models: List[Dict[str, str]] = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.lower().startswith("available models"):
            continue
        match = _AGENT_LINE.match(line)
        if not match:
            continue
        model_id, name = match.group(1), match.group(2).strip()
        models.append({"id": model_id, "name": name, "weight": classify_weight(model_id, name)})
    return models


def _discover_agy(binary: str) -> List[Dict[str, str]]:
    code, out = _run_cli([binary, "models"])
    models: List[Dict[str, str]] = []
    for line in out.splitlines():
        token = line.strip()
        if not token or not _AGY_ID.match(token):
            continue
        if " " in token or token.startswith("E") and ":" in line:
            continue
        label = token.replace("-", " ")
        models.append({"id": token, "name": label, "weight": classify_weight(token, label)})
    return models


def _discover_codex(_binary: str) -> List[Dict[str, str]]:
    return [dict(m) for m in FALLBACK_CATALOG["codex"]]


def _discover_claude(_binary: str) -> List[Dict[str, str]]:
    return [dict(m) for m in FALLBACK_CATALOG["claude"]]


def _discover_openclaude(_binary: str) -> List[Dict[str, str]]:
    return [dict(m) for m in FALLBACK_CATALOG["openclaude"]]


def _discover_opencode(binary: str) -> List[Dict[str, str]]:
    """`opencode models` lista um id por linha (provider/modelo); os *-free são gratuitos."""
    code, out = _run_cli([binary, "models"], timeout=30)
    models: List[Dict[str, str]] = []
    for line in out.splitlines():
        token = line.strip()
        if not token or " " in token or "/" not in token:
            continue
        name = token.rsplit("/", 1)[-1].replace("-", " ")
        weight = "light" if token.endswith("-free") else classify_weight(token, name)
        models.append({"id": token, "name": name, "weight": weight})
    return models


_DISCOVERERS = {
    "agent": _discover_agent,
    "agy": _discover_agy,
    "codex": _discover_codex,
    "claude": _discover_claude,
    "openclaude": _discover_openclaude,
    "opencode": _discover_opencode,
}


def _load_cache() -> dict:
    if not MODELS_CATALOG_FILE.exists():
        return {}
    try:
        return json.loads(MODELS_CATALOG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(data: dict) -> None:
    MODELS_CATALOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    MODELS_CATALOG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def cache_is_stale(data: Optional[dict] = None) -> bool:
    payload = data if data is not None else _load_cache()
    updated = _parse_iso(payload.get("updated_at", ""))
    if updated is None:
        return True
    age_days = (time.time() - updated) / 86400.0
    ttl = float(payload.get("ttl_days", MODELS_TTL_DAYS))
    return age_days >= ttl


def refresh_model_catalog(*, force: bool = False) -> dict:
    """Consulta CLIs e grava cache. Se force=False e cache fresco, só devolve o cache."""
    with _LOCK:
        current = _load_cache()
        if not force and current and not cache_is_stale(current):
            return current

        providers: Dict[str, dict] = {}
        for provider_id, discover in _DISCOVERERS.items():
            binary = None
            if provider_id == "agent":
                binary = which("agent") or which("cursor-agent")
            else:
                binary = which(provider_id)

            models: List[Dict[str, str]] = []
            source = "fallback"
            if binary:
                try:
                    models = discover(binary)
                except Exception:
                    models = []
            if models:
                source = "live"
            else:
                models = [dict(m) for m in FALLBACK_CATALOG.get(provider_id, [])]

            providers[provider_id] = {
                "models": models,
                "recommended_default_model": _pick_recommended(provider_id, models),
                "source": source,
                "binary": binary,
            }

        payload = {
            "updated_at": _now_iso(),
            "ttl_days": MODELS_TTL_DAYS,
            "providers": providers,
        }
        _save_cache(payload)
        return payload


def ensure_models_fresh(*, force_session: bool = False) -> dict:
    global _SESSION_ENSURED
    with _LOCK:
        already = _SESSION_ENSURED
    if already and not force_session:
        data = _load_cache()
        if data and not cache_is_stale(data):
            return data
    data = refresh_model_catalog(force=force_session or cache_is_stale())
    with _LOCK:
        _SESSION_ENSURED = True
    return data


def get_provider_models(provider_id: str) -> List[Dict[str, str]]:
    data = ensure_models_fresh()
    entry = (data.get("providers") or {}).get(provider_id) or {}
    models = entry.get("models")
    if models:
        return list(models)
    return [dict(m) for m in FALLBACK_CATALOG.get(provider_id, [])]


def get_recommended_default_model(provider_id: str) -> Optional[str]:
    data = ensure_models_fresh()
    entry = (data.get("providers") or {}).get(provider_id) or {}
    recommended = entry.get("recommended_default_model")
    if recommended:
        return recommended
    return FALLBACK_DEFAULTS.get(provider_id)


def catalog_meta() -> dict:
    data = ensure_models_fresh()
    return {
        "updated_at": data.get("updated_at"),
        "ttl_days": data.get("ttl_days", MODELS_TTL_DAYS),
        "stale": cache_is_stale(data),
        "cache_file": str(MODELS_CATALOG_FILE),
    }


def is_heavy_model(provider_id: str, model: Optional[str]) -> bool:
    if not model:
        return False
    key = model.strip().lower()
    for entry in get_provider_models(provider_id):
        if entry.get("id", "").lower() == key or entry.get("name", "").lower() == key:
            return entry.get("weight") == "heavy"
    return False


def model_in_catalog(provider_id: str, model: str) -> bool:
    key = model.strip().lower()
    for entry in get_provider_models(provider_id):
        if entry.get("id", "").lower() == key:
            return True
        if entry.get("name", "").lower() == key:
            return True
    return False


def resolve_model(provider_id: str, model: Optional[str]) -> Optional[str]:
    recommended = get_recommended_default_model(provider_id)

    def _to_id(value: str) -> Optional[str]:
        key = value.strip().lower()
        for entry in get_provider_models(provider_id):
            if entry.get("id", "").lower() == key:
                return entry["id"]
            if entry.get("name", "").lower() == key:
                return entry["id"]
        return None

    if model is None:
        chosen = recommended
    else:
        matched = _to_id(model)
        chosen = matched if matched is not None else recommended

    if chosen and chosen.lower() == "auto":
        return None
    return chosen


def legacy_catalog_for_provider(provider_id: str) -> List[Dict[str, str]]:
    from athena.ratings import ratings_for_model, ROLE_LABELS

    out = []
    for entry in get_provider_models(provider_id):
        item = {
            "id": entry.get("id", entry.get("name", "")),
            "name": entry.get("name", entry.get("id", "")),
            "weight": entry.get("weight", "medium"),
        }
        rating = ratings_for_model(item["id"], item["name"])
        if rating:
            item["scores"] = rating.get("scores", {})
            item["tags"] = [ROLE_LABELS[r] for r in rating.get("best_for", []) if r in ROLE_LABELS]
            item["rating_note"] = rating.get("note", "")
        out.append(item)
    return out
