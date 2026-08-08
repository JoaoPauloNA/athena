"""Recomendador de provider/modelo por tarefa, baseado na tabela de notas.

O orquestrador descreve a tarefa; o MCP classifica a(s) função(ões)
(frontend, backend, raciocínio, rapidez), cruza a tabela de ratings com os
providers INSTALADOS na máquina e devolve quem chamar, com modelo e motivo.
"""

from __future__ import annotations

import re

from athena.ratings import ROLE_LABELS, load_ratings

# Sinais de complexidade da tarefa
_SIMPLE_HINTS = [
    "renomear", "rename", "typo", "comentário", "comentario", "format",
    "lint", "simples", "trivial", "pequen", "rápido", "rapido", "quick",
    "uma linha", "one-liner", "texto", "string", "constante", "import",
]
_COMPLEX_HINTS = [
    "arquitetura", "migração", "migracao", "concorrência", "concorrencia",
    "segurança", "seguranca", "multi-arquivo", "multifile", "sistema inteiro",
    "refatorar tudo", "redesign", "do zero", "from scratch", "performance",
    "otimizar", "escala", "distribuído", "distribuido", "race condition",
    "deadlock", "memory leak", "vazamento",
]


def _keyword_hit(text: str, keyword: str) -> bool:
    """Match por palavra inteira: 'design' não casa com 'redesign'."""
    return re.search(rf"(?<![\w-]){re.escape(keyword)}(?![\w-])", text) is not None


def estimate_complexity(task_description: str) -> str:
    """simple | medium | complex — guia de economia de modelo."""
    text = (task_description or "").lower()
    if any(_keyword_hit(text, h) for h in _COMPLEX_HINTS):
        return "complex"
    if any(_keyword_hit(text, h) for h in _SIMPLE_HINTS):
        return "simple"
    return "medium"


# Peso máximo de modelo recomendado por complexidade
_MAX_WEIGHT_BY_COMPLEXITY = {"simple": "light", "medium": "medium", "complex": "heavy"}
_WEIGHT_ORDER = {"light": 0, "medium": 1, "heavy": 2}


# Palavras-chave que mapeiam a descrição da tarefa para cada função
_TASK_KEYWORDS: dict[str, list[str]] = {
    "frontend": [
        "frontend", "front-end", "ui", "ux", "interface", "tela", "layout",
        "css", "tailwind", "componente", "react", "vue", "design", "landing",
        "dashboard", "visual", "página", "pagina", "estilo", "responsivo",
        "html", "figma", "animação", "animacao",
    ],
    "backend": [
        "backend", "back-end", "api", "endpoint", "banco", "database", "sql",
        "refactor", "refatorar", "migração", "migracao", "teste", "test",
        "bug", "fix", "corrig", "implement", "feature", "cli", "script",
        "pipeline", "agent", "agente", "automação", "automacao", "código", "codigo",
    ],
    "raciocinio": [
        "raciocínio", "raciocinio", "reasoning", "arquitetura", "planejar",
        "plan", "decisão", "decisao", "analise", "análise", "complex",
        "difícil", "dificil", "algoritmo", "matemática", "matematica",
        "debug", "investigar", "diagnostic", "trade-off", "estratégia", "estrategia",
    ],
    "rapidez": [
        "rápido", "rapido", "simples", "pequen", "barato", "cheap", "fast",
        "quick", "trivial", "renomear", "rename", "typo", "comentário",
        "comentario", "format", "lint", "grátis", "gratis", "free",
    ],
}


def classify_task(task_description: str, task_type: str | None = None) -> dict[str, float]:
    """Pontua cada função para a tarefa (0-1). task_type explícito tem prioridade."""
    if task_type and task_type in ROLE_LABELS:
        return {task_type: 1.0}
    text = (task_description or "").lower()
    scores: dict[str, float] = {}
    for role, keywords in _TASK_KEYWORDS.items():
        hits = sum(1 for kw in keywords if _keyword_hit(text, kw))
        if hits:
            scores[role] = min(1.0, hits / 2.0)
    if not scores:
        # default: backend agentic é a tarefa mais comum de um orquestrador de CLIs
        scores = {"backend": 0.6}
    return scores


def _providers_by_model() -> dict[str, list[dict]]:
    """Mapa needle → providers instalados cujos catálogos contêm o modelo."""
    from athena.providers import list_providers

    mapping: dict[str, list[dict]] = {}
    for p in list_providers():
        if not p.get("available"):
            continue
        for m in p.get("models", []):
            key = f"{m.get('id', '')} {m.get('name', '')}".lower()
            mapping.setdefault(key, []).append({
                "provider": p["id"],
                "provider_name": p["name"],
                "model_id": m.get("id"),
                "model_name": m.get("name"),
                "weight": m.get("weight", "medium"),
            })
    return mapping


def recommend_for_task(
    task_description: str,
    *,
    task_type: str | None = None,
    top_n: int = 3,
    only_installed: bool = True,
    use_reliability: bool = True,
) -> dict:
    """Recomenda provider+modelo para uma tarefa, com base nas notas.

    Com use_reliability=True (padrão), o histórico de vereditos persistidos
    (~/.athena/verdicts.json) ajusta a ordenação: provider com muitos
    relatórios FALSOS verificados é rebaixado e sinalizado com aviso.
    """
    role_scores = classify_task(task_description, task_type)
    primary_role = max(role_scores, key=role_scores.get)
    complexity = estimate_complexity(task_description)
    max_weight = _MAX_WEIGHT_BY_COMPLEXITY[complexity]

    data = load_ratings()
    catalog = _providers_by_model()

    reliability: dict = {}
    if use_reliability:
        from athena.reliability import reliability_report
        reliability = reliability_report()

    suggestions: list[dict] = []
    excluded_heavy: list[str] = []
    for entry in data.get("models", []):
        scores = entry.get("scores") or {}
        # nota ponderada pelas funções detectadas na tarefa
        weighted = sum(scores.get(role, 0) * weight for role, weight in role_scores.items())
        weighted /= max(sum(role_scores.values()), 1e-9)

        # quais providers instalados têm esse modelo?
        installed_in: list[dict] = []
        for key, providers in catalog.items():
            if any(needle.lower() in key for needle in entry.get("match", [])):
                installed_in.extend(providers)
        # dedup por provider+modelo
        seen = set()
        unique = []
        for item in installed_in:
            k = (item["provider"], item["model_id"])
            if k not in seen:
                seen.add(k)
                unique.append(item)

        # economia: tarefa simples/média não recomenda modelo acima do peso permitido
        allowed, heavy_ones = [], []
        for item in unique:
            if _WEIGHT_ORDER.get(item.get("weight", "medium"), 1) > _WEIGHT_ORDER[max_weight]:
                heavy_ones.append(item)
            else:
                allowed.append(item)
        if heavy_ones and not allowed:
            excluded_heavy.append(entry.get("name", "?"))
            continue
        unique = allowed

        if only_installed and not unique:
            continue

        # Anota confiabilidade do histórico de vereditos em cada provider.
        warnings: list[str] = []
        best_conf: float | None = None
        for item in unique:
            stats = reliability.get(item["provider"])
            if not stats:
                continue
            item["confiabilidade"] = stats.get("confiabilidade")
            item["taxa_falso"] = stats.get("taxa_falso")
            item["episodios"] = stats.get("episodios", 0)
            conf = stats.get("confiabilidade")
            if conf is not None:
                best_conf = conf if best_conf is None else max(best_conf, conf)
            decididos = stats.get("verdadeiros", 0) + stats.get("falsos", 0)
            if (stats.get("taxa_falso") or 0) >= 0.5 and decididos >= 3:
                warnings.append(
                    f"⚠️ '{item['provider']}' tem {round(stats['taxa_falso'] * 100)}% de "
                    f"relatórios FALSOS verificados ({decididos} episódios)."
                )

        # nota ajustada: histórico pesa 30% quando existe (70% nota pública).
        nota_ajustada = weighted if best_conf is None else weighted * (0.7 + 0.3 * best_conf)

        suggestions.append({
            "modelo": entry.get("name"),
            "maker": entry.get("maker"),
            "nota": round(weighted, 1),
            "nota_ajustada": round(nota_ajustada, 1),
            "installed": bool(unique),
            "onde": unique[:3],
            "motivo": entry.get("note", ""),
            "avisos_confianca": warnings,
        })

    suggestions.sort(key=lambda s: (s["installed"], s["nota_ajustada"]), reverse=True)
    top = suggestions[:top_n]

    return {
        "tarefa": task_description,
        "complexidade": complexity,
        "funcao_detectada": {"role": primary_role, "label": ROLE_LABELS[primary_role]},
        "funcoes_secundarias": [
            {"role": r, "label": ROLE_LABELS[r], "peso": w}
            for r, w in sorted(role_scores.items(), key=lambda x: -x[1])
            if r != primary_role
        ],
        "recomendacoes": top,
        "economia": (
            f"Tarefa '{complexity}': modelos acima de '{max_weight}' foram excluídos "
            f"das recomendações ({', '.join(excluded_heavy)})."
            if excluded_heavy else
            f"Tarefa '{complexity}': nenhum modelo pesado precisou ser excluído."
        ),
        "dica": _build_tip(top, primary_role),
        "confianca": (
            "Histórico de vereditos aplicado: notas ajustadas em até 30% pela "
            "confiabilidade local (claimed vs verified) de cada provider."
            if reliability else
            "Sem histórico de vereditos ainda — rode tarefas com verify=true "
            "para calibrar as recomendações pela confiabilidade real."
        ),
    }


def _build_tip(top: list[dict], role: str) -> str:
    if not top:
        return "Nenhum modelo da tabela disponível — instale um provider ou rode refresh_models."
    best = top[0]
    if best["installed"] and best["onde"]:
        onde = best["onde"][0]
        return (
            f"Para {ROLE_LABELS[role]}, chame o provider '{onde['provider']}' "
            f"com model '{onde['model_id']}' ({best['modelo']}, nota {best['nota']}/10)."
        )
    return (
        f"O melhor para {ROLE_LABELS[role]} seria {best['modelo']} ({best['maker']}), "
        "mas não está instalado; veja alternativas instaladas abaixo."
    )
