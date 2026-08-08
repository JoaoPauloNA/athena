"""Tabela de notas dos modelos por função (frontend, backend, raciocínio, velocidade).

As notas vivem em ~/.athena/model_ratings.json (cache com TTL) e são
atualizadas automaticamente pela Automation semanal "Athena · Model Ratings
refresh", que pesquisa leaderboards na internet e regrava o arquivo.
Se o cache não existir ou estiver inválido, usa-se o RATINGS_SEED abaixo.

Formato de cada entrada:
    {
        "match": ["gpt-5.6", "gpt-5.5"],   # substrings p/ casar com ids/nomes do catálogo
        "name": "GPT-5.6 Sol",
        "maker": "OpenAI",
        "scores": {"frontend": 8, "backend": 10, "raciocinio": 9, "rapidez": 6},
        "best_for": ["backend"],
        "note": "Líder SWE-bench 96.2% (Vals AI, jul/2026).",
        "sources": ["https://www.morphllm.com/best-ai-model-for-coding"],
    }

Notas de 0 a 10. `best_for` usa as chaves de ROLE_LABELS.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from athena.config import DATA_DIR

RATINGS_FILE = DATA_DIR / "model_ratings.json"
RATINGS_TTL_DAYS = float(os.environ.get("ATHENA_RATINGS_TTL_DAYS", "7"))

ROLE_LABELS: Dict[str, str] = {
    "frontend": "Frontend / UI",
    "backend": "Backend / Agentic",
    "raciocinio": "Raciocínio",
    "rapidez": "Rápido & Barato",
}

# --- Seed (pesquisa de jul/ago 2026; a Automation sobrescreve com dados novos) ---

RATINGS_SEED: List[dict] = [
    {
        "match": ["gpt-5.6", "gpt-5.5"],
        "name": "GPT-5.6 Sol",
        "maker": "OpenAI",
        "scores": {"frontend": 8, "backend": 10, "raciocinio": 9, "rapidez": 6},
        "best_for": ["backend"],
        "note": "Líder SWE-bench Verified 96.2% (harness independente Vals AI, jul/2026).",
        "sources": ["https://www.morphllm.com/best-ai-model-for-coding"],
    },
    {
        "match": ["fable-5", "fable 5"],
        "name": "Claude Fable 5",
        "maker": "Anthropic",
        "scores": {"frontend": 9, "backend": 9, "raciocinio": 9, "rapidez": 5},
        "best_for": ["backend"],
        "note": "95.0% SWE-bench; líder em Computer Use (OSWorld 85).",
        "sources": ["https://www.morphllm.com/best-ai-model-for-coding", "https://www.vellum.ai/llm-leaderboard"],
    },
    {
        "match": ["kimi-k3", "kimi k3", "k3"],
        "name": "Kimi K3",
        "maker": "Moonshot AI",
        "scores": {"frontend": 10, "backend": 9, "raciocinio": 8, "rapidez": 7},
        "best_for": ["frontend"],
        "note": "#1 no Frontend Code Arena e Design Arena (Elo 1378); 93.4% SWE-bench.",
        "sources": ["https://modelgrep.com/best/design", "https://www.morphllm.com/best-ai-model-for-coding"],
    },
    {
        "match": ["opus"],
        "name": "Claude Opus 4.8",
        "maker": "Anthropic",
        "scores": {"frontend": 8, "backend": 9, "raciocinio": 9, "rapidez": 4},
        "best_for": ["raciocinio", "backend"],
        "note": "88.6% SWE-bench; 'pay once, save an hour' para refactors profundos.",
        "sources": ["https://www.builder.io/blog/best-llms-for-coding"],
    },
    {
        "match": ["sonnet"],
        "name": "Claude Sonnet 5",
        "maker": "Anthropic",
        "scores": {"frontend": 9, "backend": 8, "raciocinio": 10, "rapidez": 6},
        "best_for": ["raciocinio", "frontend"],
        "note": "Líder GPQA Diamond 96.2%; componentes React mais limpos em testes práticos.",
        "sources": ["https://www.vellum.ai/llm-leaderboard", "https://www.buildfastwithai.com/blogs/best-ai-models-frontend-ui-development-2026"],
    },
    {
        "match": ["gemini-3.1-pro", "gemini 3.1 pro"],
        "name": "Gemini 3.1 Pro",
        "maker": "Google",
        "scores": {"frontend": 9, "backend": 8, "raciocinio": 9, "rapidez": 6},
        "best_for": ["frontend"],
        "note": "'UI-first instincts'; líder ARC-AGI-2 e multimodal (GPQA 94.3%).",
        "sources": ["https://www.builder.io/blog/best-llms-for-coding", "https://iternal.ai/llm-selection-guide"],
    },
    {
        "match": ["gemini-3.6-flash", "gemini-3.5-flash", "gemini 3.1 flash", "flash"],
        "name": "Gemini 3.x Flash",
        "maker": "Google",
        "scores": {"frontend": 7, "backend": 7, "raciocinio": 7, "rapidez": 9},
        "best_for": ["rapidez"],
        "note": "Value sprinter: rápido e barato com bons instintos de código.",
        "sources": ["https://www.builder.io/blog/best-llms-for-coding"],
    },
    {
        "match": ["haiku"],
        "name": "Claude Haiku 4.5",
        "maker": "Anthropic",
        "scores": {"frontend": 6, "backend": 6, "raciocinio": 6, "rapidez": 10},
        "best_for": ["rapidez"],
        "note": "O 'runner': sempre ligado, ideal para loops de ferramentas ($1/$5 por M tokens).",
        "sources": ["https://www.builder.io/blog/best-llms-for-coding"],
    },
    {
        "match": ["composer"],
        "name": "Cursor Composer 2.5",
        "maker": "Cursor",
        "scores": {"frontend": 7, "backend": 8, "raciocinio": 7, "rapidez": 8},
        "best_for": ["rapidez"],
        "note": "Default do Cursor Agent; loop rápido de edição repo-native.",
        "sources": [],
    },
    {
        "match": ["grok"],
        "name": "Grok 4.5",
        "maker": "xAI",
        "scores": {"frontend": 7, "backend": 8, "raciocinio": 8, "rapidez": 6},
        "best_for": ["backend"],
        "note": "Competitivo em HLE e agentic coding.",
        "sources": ["https://iternal.ai/llm-selection-guide"],
    },
    {
        "match": ["gpt-oss", "gpt-5.2", "o4-mini"],
        "name": "GPT mid-tier (5.2 / o4-mini / GPT-OSS)",
        "maker": "OpenAI",
        "scores": {"frontend": 6, "backend": 7, "raciocinio": 7, "rapidez": 8},
        "best_for": ["rapidez"],
        "note": "Custo-benefício para tarefas médias.",
        "sources": [],
    },
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str) -> Optional[float]:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _seed_payload() -> dict:
    return {
        "updated_at": _now_iso(),
        "ttl_days": RATINGS_TTL_DAYS,
        "roles": ROLE_LABELS,
        "models": [dict(m) for m in RATINGS_SEED],
    }


def load_ratings() -> dict:
    """Carrega o cache de notas; se não existir/estiver inválido, grava o seed."""
    if RATINGS_FILE.exists():
        try:
            data = json.loads(RATINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(data.get("models"), list) and data["models"]:
                data.setdefault("roles", ROLE_LABELS)
                return data
        except (OSError, json.JSONDecodeError):
            pass
    data = _seed_payload()
    save_ratings(data)
    return data


def save_ratings(data: dict) -> None:
    RATINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    RATINGS_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def ratings_meta() -> dict:
    data = load_ratings()
    updated = _parse_iso(data.get("updated_at", ""))
    age_days = (time.time() - updated) / 86400.0 if updated else None
    ttl = float(data.get("ttl_days", RATINGS_TTL_DAYS))
    return {
        "updated_at": data.get("updated_at"),
        "ttl_days": ttl,
        "stale": age_days is None or age_days >= ttl,
        "cache_file": str(RATINGS_FILE),
    }


def _match_score(entry: dict, model_id: str, model_name: str) -> int:
    """Quão bem uma entrada da tabela casa com um modelo do catálogo (0 = não casa)."""
    hay = f"{model_id} {model_name}".lower()
    best = 0
    for needle in entry.get("match", []):
        needle = needle.lower()
        if needle in hay:
            best = max(best, len(needle))
    return best


def ratings_for_model(model_id: str, model_name: str = "") -> Optional[dict]:
    """Retorna a entrada da tabela que melhor casa com o modelo, se houver."""
    data = load_ratings()
    best_entry, best_score = None, 0
    for entry in data.get("models", []):
        score = _match_score(entry, model_id, model_name)
        if score > best_score:
            best_entry, best_score = entry, score
    return best_entry


def best_per_role(available_model_keys: Optional[List[str]] = None) -> Dict[str, List[dict]]:
    """Melhores modelos por função, ordenados pela nota da função.

    Se `available_model_keys` for informado (ids/nomes de modelos de providers
    instalados), entradas que não casam com nada instalado são marcadas com
    `installed: False`.
    """
    data = load_ratings()
    roles: Dict[str, List[dict]] = {role: [] for role in ROLE_LABELS}
    for entry in data.get("models", []):
        hay_available = True
        if available_model_keys is not None:
            hay = " ".join(available_model_keys).lower()
            hay_available = any(n.lower() in hay for n in entry.get("match", []))
        for role, label in ROLE_LABELS.items():
            score = (entry.get("scores") or {}).get(role, 0)
            roles[role].append({
                "name": entry.get("name"),
                "maker": entry.get("maker"),
                "score": score,
                "best_for": entry.get("best_for", []),
                "note": entry.get("note", ""),
                "installed": hay_available,
            })
    for role in roles:
        roles[role].sort(key=lambda e: e["score"], reverse=True)
    return roles


def get_ratings_payload(installed_keys: Optional[List[str]] = None) -> dict:
    """Payload completo para a API / dashboard."""
    data = load_ratings()
    return {
        "meta": ratings_meta(),
        "roles": ROLE_LABELS,
        "best_per_role": best_per_role(installed_keys),
        "models": data.get("models", []),
    }
