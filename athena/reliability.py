"""Persistência de vereditos e ranking local de confiabilidade (claimed vs verified).

Cada episódio de verificação (uma alegação de um executor confrontada com a
verificação) vira um registro REDIGIDO em ~/.athena/verdicts.json — sem
prompts, sem relatórios completos, sem caminhos sensíveis. Com o acúmulo,
reliability_report() agrega por CLI a taxa local de claimed-vs-verified:

    quantas vezes cada CLI disse "pronto" e era verdade?

Este é o mesmo número que o produto Verificador quer publicar em escala
global — aqui em escala individual, validando o protocolo.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from athena.config import DATA_DIR

VERDICTS_FILE = Path(os.environ.get("ATHENA_VERDICTS_FILE", str(DATA_DIR / "verdicts.json")))

_LOCK = threading.Lock()
_MAX_RECORDS = 500          # retenção: últimos 500 episódios
_MAX_MOTIVO_LEN = 120       # motivos truncados (telemetria redigida)
_MAX_MOTIVOS = 3


def _load() -> list[dict]:
    if not VERDICTS_FILE.exists():
        return []
    try:
        data = json.loads(VERDICTS_FILE.read_text())
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save(records: list[dict]) -> None:
    VERDICTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    VERDICTS_FILE.write_text(json.dumps(records[-_MAX_RECORDS:], indent=2, ensure_ascii=False))


def record_verdict(
    executor_provider: str,
    verdict,
    *,
    task_excerpt: str = "",
    project: str = "",
) -> None:
    """Grava um episódio de verificação (redigido: sem prompt/relatório completo).

    `verdict` é um athena.verifier.Verdict. verdadeiro=None conta como
    episódio "indisponível" (não entra na taxa, mas fica na telemetria).
    """
    motivos = [str(m)[:_MAX_MOTIVO_LEN] for m in (verdict.motivos or [])][:_MAX_MOTIVOS]
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "executor": executor_provider or "desconhecido",
        "camada": "deterministic" if verdict.verificador == "deterministic" else "advisory",
        "verificador": verdict.verificador,
        "verdadeiro": verdict.verdadeiro,
        "confianca": verdict.confianca,
        "motivos": motivos,
        "tentativas": verdict.tentativas,
        "escalado": verdict.escalado,
        "tarefa": (task_excerpt or "")[:80],
        "projeto": Path(project).name if project else "",
    }
    with _LOCK:
        records = _load()
        records.append(record)
        _save(records)


def list_verdicts(limit: int = 50) -> list[dict]:
    with _LOCK:
        return _load()[-limit:]


def reliability_report() -> dict[str, dict]:
    """Ranking local de confiabilidade por CLI (claimed vs verified)."""
    with _LOCK:
        records = _load()

    report: dict[str, dict] = {}
    for r in records:
        executor = r.get("executor", "desconhecido")
        entry = report.setdefault(
            executor,
            {
                "episodios": 0,
                "verdadeiros": 0,
                "falsos": 0,
                "indisponiveis": 0,
                "escalados": 0,
                "taxa_falso": None,
                "confiabilidade": None,
                "ultimo": None,
            },
        )
        entry["episodios"] += 1
        entry["ultimo"] = r.get("ts")
        if r.get("escalado"):
            entry["escalados"] += 1
        v = r.get("verdadeiro")
        if v is True:
            entry["verdadeiros"] += 1
        elif v is False:
            entry["falsos"] += 1
        else:
            entry["indisponiveis"] += 1

    for entry in report.values():
        decididos = entry["verdadeiros"] + entry["falsos"]
        if decididos:
            entry["taxa_falso"] = round(entry["falsos"] / decididos, 3)
            entry["confiabilidade"] = round(entry["verdadeiros"] / decididos, 3)

    # Mais confiável primeiro; sem dados decididos vão para o fim.
    return dict(
        sorted(
            report.items(),
            key=lambda kv: (kv[1]["confiabilidade"] is not None, kv[1]["confiabilidade"] or 0),
            reverse=True,
        )
    )
