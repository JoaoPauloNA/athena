"""Persistência de vereditos e ranking local de confiabilidade (claimed vs verified).

Cada episódio de verificação (uma alegação de um executor confrontada com a
verificação) vira um registro REDIGIDO em ~/.athena/verdicts.json — sem
prompts, sem relatórios completos, sem caminhos sensíveis. Com o acúmulo,
reliability_report() agrega por CLI a taxa local de claimed-vs-verified:

    quantas vezes uma alegação decidida pelo pipeline recebeu veredito
    verdadeiro ou falso?

Essas estatísticas são telemetria local do pipeline, não uma métrica pública
de correção geral do provider.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from athena.config import DATA_DIR

VERDICTS_FILE = Path(os.environ.get("ATHENA_VERDICTS_FILE", str(DATA_DIR / "verdicts.json")))

_LOCK = threading.Lock()
_MAX_RECORDS = 500          # retenção: últimos 500 episódios
_SAFE_REASON_CODES = {
    "client_abandoned",
    "user_requested",
    "absolute_deadline",
    "idle_deadline",
    "verification_deadline",
    "combo_overall_deadline",
    "command_not_found",
    "natural_exit_cleanup",
    "termination_unconfirmed",
    "internal_error",
    "other_redacted",
}


_IDENTIFIER_MARKERS = (
    "token",
    "secret",
    "password",
    "credential",
    "authorization",
    "bearer",
    "api_key",
    "apikey",
)
_TOKEN_PREFIXES = ("sk-", "ghp_", "gho_", "xoxb-", "xoxp-", "ya29.")


def _looks_high_entropy(text: str) -> bool:
    if len(text) < 20:
        return False
    if not all(ch.isalnum() or ch in "._-~" for ch in text):
        return False
    has_alpha = any(ch.isalpha() for ch in text)
    has_digit = any(ch.isdigit() for ch in text)
    unique_ratio = len(set(text)) / len(text)
    return has_alpha and has_digit and unique_ratio >= 0.55


def _sanitize_identifier(
    value: object,
    *,
    fallback: str,
    max_len: int = 64,
    allow_slash_pair: bool = False,
) -> str:
    text = str(value or "").strip().lower()
    if not text or len(text) > max_len:
        return fallback
    if any(marker in text for marker in _IDENTIFIER_MARKERS):
        return fallback
    if any(prefix in text for prefix in _TOKEN_PREFIXES):
        return fallback
    if any(ch.isspace() for ch in text) or "\\" in text:
        return fallback
    if "/" in text:
        if not allow_slash_pair:
            return fallback
        parts = text.split("/")
        if len(parts) != 2:
            return fallback
        if not all(parts) or not all(
            part[0].isalnum() and all(ch.isalnum() or ch in "._-" for ch in part) and len(part) <= 32
            for part in parts
        ):
            return fallback
        return f"{parts[0]}/{parts[1]}"
    if not (text[0].isalnum() and all(ch.isalnum() or ch in "._-" for ch in text)):
        return fallback
    if _looks_high_entropy(text):
        return fallback
    return text


def _sanitize_ts(value: object) -> str:
    now_value = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if not isinstance(value, str):
        return now_value
    candidate = value.strip()
    if not candidate or len(candidate) > 64:
        return now_value
    iso_candidate = candidate.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso_candidate)
    except ValueError:
        return now_value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_confidence(value: object) -> str:
    candidate = str(value or "").strip().lower()
    if candidate in {"alta", "media", "baixa"}:
        return candidate
    return "baixa"


def _categorize_reason(value: object) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return "other_redacted"
    if text in _SAFE_REASON_CODES:
        return text
    if "client_abandoned" in text:
        return "client_abandoned"
    if "user_requested" in text or "server_shutdown" in text or "control_plane" in text:
        return "user_requested"
    if "verification_deadline" in text:
        return "verification_deadline"
    if "combo_overall_deadline" in text or "overall_timeout" in text:
        return "combo_overall_deadline"
    if "idle_deadline" in text or "inatividade" in text:
        return "idle_deadline"
    if "absolute_deadline" in text or ("timeout" in text and "idle" not in text and "combo" not in text):
        return "absolute_deadline"
    if "command_not_found" in text or "filenotfound" in text or "not found" in text:
        return "command_not_found"
    if "natural_exit_cleanup" in text:
        return "natural_exit_cleanup"
    if "termination_unconfirmed" in text:
        return "termination_unconfirmed"
    if "internal" in text or "error" in text or "exception" in text:
        return "internal_error"
    return "other_redacted"


def _sanitize_attempt_count(value: object) -> int:
    if isinstance(value, bool):
        return 1
    try:
        parsed = int(value or 1)
    except (TypeError, ValueError):
        return 1
    return max(1, min(parsed, 1000))


def _sanitize_record(raw: dict) -> dict:
    motivos = raw.get("reason_codes", raw.get("motivos", []))
    if not isinstance(motivos, list):
        motivos = [motivos]
    tentativas = _sanitize_attempt_count(raw.get("tentativas"))
    verdadeiro_raw = raw.get("verdadeiro")
    verdadeiro = verdadeiro_raw if isinstance(verdadeiro_raw, bool) or verdadeiro_raw is None else None
    escalado_raw = raw.get("escalado")
    escalado = escalado_raw if isinstance(escalado_raw, bool) else False
    return {
        "ts": _sanitize_ts(raw.get("ts")),
        "executor": _sanitize_identifier(raw.get("executor"), fallback="redacted_identifier"),
        "camada": "deterministic" if str(raw.get("camada")) == "deterministic" else "advisory",
        "verificador": _sanitize_identifier(
            raw.get("verificador"),
            fallback="redacted_identifier",
            max_len=96,
            allow_slash_pair=True,
        ),
        "verdadeiro": verdadeiro,
        "confianca": _normalize_confidence(raw.get("confianca")),
        "reason_codes": [_categorize_reason(reason) for reason in motivos][:3],
        "tentativas": tentativas,
        "escalado": escalado,
    }


def _load() -> list[dict]:
    if not VERDICTS_FILE.exists():
        return []
    try:
        data = json.loads(VERDICTS_FILE.read_text())
        if not isinstance(data, list):
            return []
        out: list[dict] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                out.append(_sanitize_record(item))
            except Exception:
                continue
        return out
    except (json.JSONDecodeError, OSError, ValueError, TypeError):
        return []


def _save(records: list[dict]) -> None:
    VERDICTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    redacted = [_sanitize_record(item) for item in records if isinstance(item, dict)][-_MAX_RECORDS:]
    payload = json.dumps(redacted, indent=2, ensure_ascii=False)
    tmp_path: str | None = None
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(VERDICTS_FILE.parent),
        delete=False,
    ) as tmp:
        tmp.write(payload)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = tmp.name
    try:
        os.replace(tmp_path, VERDICTS_FILE)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


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
    del task_excerpt, project
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "executor": executor_provider or "desconhecido",
        "camada": "deterministic" if verdict.verificador == "deterministic" else "advisory",
        "verificador": verdict.verificador,
        "verdadeiro": verdict.verdadeiro,
        "confianca": _normalize_confidence(verdict.confianca),
        "reason_codes": [_categorize_reason(reason) for reason in (verdict.motivos or [])][:3],
        "tentativas": verdict.tentativas,
        "escalado": bool(verdict.escalado),
    }
    # Reliability history is local, non-critical telemetry. A filesystem or
    # serialization failure here must never discard a completed verifier
    # result or keep its workspace lease held after process termination.
    try:
        with _LOCK:
            records = _load()
            records.append(record)
            _save(records)
    except (OSError, TypeError, ValueError):
        return


def list_verdicts(limit: int = 50) -> list[dict]:
    if isinstance(limit, bool):
        safe_limit = 0
    else:
        try:
            safe_limit = int(limit)
        except (TypeError, ValueError):
            safe_limit = 0
    safe_limit = max(0, min(safe_limit, 1000))
    with _LOCK:
        if safe_limit == 0:
            return []
        return _load()[-safe_limit:]


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
