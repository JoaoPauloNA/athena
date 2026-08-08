"""Verificador ("detector de mentiras") de relatórios de CLIs executoras.

Problema: um CLI pode devolver um relatório de 10 tópicos dizendo
"9. Status final: OK" sem ter feito o trabalho de verdade. O orquestrador
(o chat) não tem como checar isso sozinho sem sujar o próprio contexto.

Solução: um modelo BARATO (por padrão modelos *-free do OpenCode) recebe
o relatório + EVIDÊNCIAS objetivas do projeto (git status/diff, arquivos
citados, mtime) e emite um veredito JSON: verdadeiro/falso + motivos.

O verificador NUNCA é o mesmo provider que executou a tarefa (anti-conluio).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# Cadeia estática de fallback (usada só se a seleção dinâmica falhar).
VERIFIER_CHAIN: List[tuple] = [
    ("opencode", "opencode/deepseek-v4-flash-free"),
    ("opencode", "opencode/nemotron-3-ultra-free"),
    ("opencode", "opencode/ling-3.0-flash-free"),
    ("ollama", None),                      # modelo default local
    ("claude", "haiku"),
    ("agy", "gemini-3.6-flash-medium"),
    ("codex", "o4-mini"),
]

MAX_FIX_ATTEMPTS = 2  # FALSO 2x → escala para o orquestrador

_FILES_CLAIMED_RE = re.compile(r"[\w./-]+\.(?:py|js|ts|tsx|jsx|json|md|html|css|sql|yaml|yml|toml|txt)", re.I)


@dataclass
class Verdict:
    verdadeiro: Optional[bool]           # None = verificação indisponível
    confianca: str = "baixa"
    motivos: List[str] = field(default_factory=list)
    evidencias: str = ""
    verificador: str = ""                # provider/modelo que verificou
    tentativas: int = 1
    escalado: bool = False               # True = FALSO 2x → orquestrador decide

    def to_dict(self) -> dict:
        return {
            "verdadeiro": self.verdadeiro,
            "confianca": self.confianca,
            "motivos": self.motivos,
            "verificador": self.verificador,
            "tentativas": self.tentativas,
            "escalado": self.escalado,
        }


def collect_evidence(working_directory: Optional[str], report: str) -> str:
    """Coleta evidências OBJETIVAS do projeto (sem rodar nada destrutivo)."""
    parts: List[str] = []
    wd = working_directory or os.getcwd()

    # git rev-parse sobe a árvore até achar o repo (wd pode ser um subdiretório)
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=wd,
            capture_output=True, text=True, timeout=10,
        )
        repo_root = top.stdout.strip() if top.returncode == 0 else None
    except (subprocess.TimeoutExpired, OSError):
        repo_root = None

    if repo_root:
        try:
            status = subprocess.run(
                ["git", "status", "--short"], cwd=wd,
                capture_output=True, text=True, timeout=10,
            ).stdout.strip()
            diffstat = subprocess.run(
                ["git", "diff", "--stat", "HEAD"], cwd=wd,
                capture_output=True, text=True, timeout=10,
            ).stdout.strip()
            parts.append(f"repo git: {repo_root}")
            parts.append(f"git status --short:\n{status or '(limpo — nada modificado)'}")
            parts.append(f"git diff --stat HEAD:\n{diffstat or '(sem diff)'}")
        except (subprocess.TimeoutExpired, OSError) as exc:
            parts.append(f"(git indisponível: {exc})")
    else:
        parts.append(f"(diretório {wd} não está dentro de um repo git)")

    claimed = sorted(set(_FILES_CLAIMED_RE.findall(report or "")))[:20]
    if claimed:
        lines = []
        for rel in claimed:
            path = Path(wd) / rel
            if path.exists():
                age_min = (time.time() - path.stat().st_mtime) / 60
                lines.append(f"  EXISTE {rel} (modificado há {age_min:.0f} min)")
            else:
                lines.append(f"  NÃO EXISTE {rel}")
        parts.append("Arquivos citados no relatório:\n" + "\n".join(lines))

    return "\n\n".join(parts)


_VERIFY_PROMPT = """Você é um VERIFICADOR de relatórios técnicos. Um agente executor afirma ter concluído uma tarefa. Decida se o relatório é VERDADEIRO ou FALSO comparando-o com as evidências objetivas do projeto.

TAREFA ORIGINAL:
{task}

RELATÓRIO DO AGENTE:
{report}

EVIDÊNCIAS DO PROJETO (fatos, não opinião):
{evidence}

Regras:
- FALSO se o relatório alega alterações que não aparecem nas evidências, cita arquivos inexistentes, afirma testes que não há sinal de terem rodado, ou contradiz os fatos.
- VERDADEIRO se as evidências sustentam as alegações principais (não exija perfeição em detalhes menores).

Responda EXCLUSIVAMENTE com um JSON neste formato, sem markdown:
{{"verdadeiro": true/false, "confianca": "alta|media|baixa", "motivos": ["..."]}}"""


def _parse_verdict(output: str) -> Optional[dict]:
    """Extrai o JSON do veredito mesmo com texto em volta."""
    match = re.search(r"\{[^{}]*\"verdadeiro\"[^{}]*\}", output or "", re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        if isinstance(data.get("verdadeiro"), bool):
            return data
    except json.JSONDecodeError:
        return None
    return None


def pick_verifier(executor_provider: str) -> Optional[tuple]:
    """Escolhe AUTONOMAMENTE o melhor verificador disponível na máquina.

    Regras, em ordem:
    1. Nunca o mesmo provider que executou a tarefa (anti-conluio).
    2. Grátis primeiro: modelos *-free do OpenCode e Ollama local.
    3. Desempate pela nota de 'rapidez' da tabela de ratings (barato/rápido).
    4. Se nada casar, cai na VERIFIER_CHAIN estática.
    """
    from athena.providers import PROVIDERS, list_providers, resolve_binary
    from athena.ratings import ratings_for_model

    candidates: List[tuple] = []  # (free, rapidez, provider_id, model_id)
    for p in list_providers():
        pid = p["id"]
        if pid == executor_provider or not p.get("available"):
            continue
        best_key, best_model = None, None
        for m in p.get("models", []):
            mid = m.get("id", "")
            free = 1 if (mid.endswith("-free") or pid == "ollama") else 0
            rating = ratings_for_model(mid, m.get("name", ""))
            rapidez = ((rating or {}).get("scores") or {}).get("rapidez", 5)
            key = (free, rapidez)
            if best_key is None or key > best_key:
                best_key, best_model = key, mid or None
        if best_key is not None:
            candidates.append((best_key[0], best_key[1], pid, best_model))

    if candidates:
        candidates.sort(reverse=True)
        _, _, pid, model = candidates[0]
        return (pid, model)

    for provider_id, model in VERIFIER_CHAIN:
        if provider_id == executor_provider:
            continue
        spec = PROVIDERS.get(provider_id)
        if spec and resolve_binary(spec):
            return (provider_id, model)
    return None


def verify_report(
    task_prompt: str,
    report: str,
    *,
    working_directory: Optional[str] = None,
    executor_provider: str = "",
) -> Verdict:
    """Verifica um relatório. Retorna Verdict (verdadeiro=None se não deu pra verificar)."""
    chosen = pick_verifier(executor_provider)
    if not chosen:
        return Verdict(verdadeiro=None, motivos=["Nenhum verificador disponível."])

    provider_id, model = chosen
    evidence = collect_evidence(working_directory, report)
    prompt = _VERIFY_PROMPT.format(task=task_prompt, report=report, evidence=evidence)

    # Import tardio para evitar ciclo providers ↔ verifier
    from athena.providers import ask_provider

    result = ask_provider(
        provider_id,
        prompt,
        use_default_role=False,
        model=model,
        working_directory=working_directory,
        timeout=120,
        with_contract=False,
    )
    parsed = _parse_verdict(result.output)
    if parsed is None:
        return Verdict(
            verdadeiro=None,
            motivos=[f"Verificador {provider_id} não retornou JSON válido."],
            evidencias=evidence,
            verificador=f"{provider_id}/{model or 'default'}",
        )
    return Verdict(
        verdadeiro=parsed["verdadeiro"],
        confianca=str(parsed.get("confianca", "baixa")),
        motivos=[str(m) for m in parsed.get("motivos", [])][:5],
        evidencias=evidence,
        verificador=f"{provider_id}/{model or 'default'}",
    )


_FIX_PROMPT_PREFIX = """⚠️ SEU RELATÓRIO ANTERIOR FOI MARCADO COMO FALSO POR UM VERIFICADOR INDEPENDENTE.

Motivos apontados:
{motivos}

Evidências do projeto:
{evidence}

Corrija o problema DE VERDADE (não apenas o texto do relatório) e gere um novo relatório.

Tarefa original:
"""


def build_fix_prompt(task_prompt: str, verdict: Verdict) -> str:
    motivos = "\n".join(f"- {m}" for m in verdict.motivos) or "- relatório inconsistente com o projeto"
    return _FIX_PROMPT_PREFIX.format(motivos=motivos, evidence=verdict.evidencias) + task_prompt
