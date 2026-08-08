from __future__ import annotations

import json
import threading
import time
from typing import Dict

from athena.config import USAGE_FILE

_LOCK = threading.Lock()

# ~4 caracteres por token, estimativa grosseira.
_CHARS_PER_TOKEN = 4


def _load() -> Dict[str, dict]:
    if not USAGE_FILE.exists():
        return {}
    try:
        return json.loads(USAGE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: Dict[str, dict]) -> None:
    USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    USAGE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def record_usage(provider_id: str, *, prompt_chars: int, output_chars: int, duration_s: float) -> None:
    estimated_tokens = (prompt_chars + output_chars) // _CHARS_PER_TOKEN
    with _LOCK:
        data = _load()
        entry = data.setdefault(
            provider_id,
            {"calls": 0, "total_duration_s": 0.0, "estimated_tokens": 0, "last_used": None},
        )
        entry["calls"] += 1
        entry["total_duration_s"] = round(entry["total_duration_s"] + duration_s, 2)
        entry["estimated_tokens"] += estimated_tokens
        entry["last_used"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        _save(data)


def get_usage() -> Dict[str, dict]:
    with _LOCK:
        return _load()
