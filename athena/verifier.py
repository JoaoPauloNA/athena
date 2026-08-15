"""Verificador de alegações em relatórios de CLIs executoras.

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
import math
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from athena.bridge import run_subprocess
from athena.execution import DeadlineBudget, ExecutionControl, ExecutionRecord, ExecutionState

# Cadeia estática de fallback (usada só se a seleção dinâmica falhar).
VERIFIER_CHAIN: list[tuple] = [
    ("opencode", "opencode/deepseek-v4-flash-free"),
    ("opencode", "opencode/nemotron-3-ultra-free"),
    ("opencode", "opencode/ling-3.0-flash-free"),
    ("ollama", None),                      # modelo default local
    ("claude", "haiku"),
    ("agy", "gemini-3.6-flash-medium"),
    ("codex", "o4-mini"),
]

MAX_FIX_ATTEMPTS = 2  # FALSO 2x → escala para o orquestrador

_FILES_CLAIMED_RE = re.compile(r"[\w./-]+\.(?:py|js|ts|tsx|jsx|json|md|html|css|sql|yaml|yml|toml|txt)", re.IGNORECASE)


@dataclass
class Verdict:
    verdadeiro: bool | None           # None = verificação indisponível
    confianca: str = "baixa"
    motivos: list[str] = field(default_factory=list)
    evidencias: str = ""
    verificador: str = ""                # provider/modelo que verificou
    tentativas: int = 1
    escalado: bool = False               # True = FALSO 2x → orquestrador decide
    execution: dict | None = None

    def to_dict(self) -> dict:
        payload = {
            "verdadeiro": self.verdadeiro,
            "confianca": self.confianca,
            "motivos": self.motivos,
            "verificador": self.verificador,
            "tentativas": self.tentativas,
            "escalado": self.escalado,
        }
        if self.execution is not None:
            payload["execution"] = self.execution
        return payload


def collect_evidence(
    working_directory: str | None,
    report: str,
    *,
    budget: DeadlineBudget | None = None,
    execution_control: ExecutionControl | None = None,
) -> tuple[str, dict | None, bool]:
    """Coleta evidências OBJETIVAS do projeto (sem rodar nada destrutivo)."""
    parts: list[str] = []
    wd = working_directory or os.getcwd()
    evidence_execution: dict | None = None
    evidence_timed_out = False

    # git rev-parse sobe a árvore até achar o repo (wd pode ser um subdiretório)
    def _is_terminal_stop(execution: dict | None) -> bool:
        state = (execution or {}).get("state")
        return state in {
            ExecutionState.CANCELLED.value,
            ExecutionState.TERMINATION_UNCONFIRMED.value,
        }

    top_timeout = budget.child_timeout(10) if budget is not None else 10
    if top_timeout <= 0:
        parts.append(f"(diretório {wd} não está dentro de um repo git)")
        return "\n\n".join(parts), evidence_execution, True
    top = run_subprocess(
        "verifier",
            ["git", "rev-parse", "--show-toplevel"], cwd=wd,
            timeout=top_timeout,
            execution_control=execution_control,
            service_profile="verification",
        )
    if top.execution is not None:
        evidence_execution = top.execution
        if _is_terminal_stop(top.execution):
            parts.append(f"(diretório {wd} não está dentro de um repo git)")
            return "\n\n".join(parts), evidence_execution, top.timed_out
    evidence_timed_out = evidence_timed_out or top.timed_out
    repo_root = (top.stdout or "").strip() if top.exit_code == 0 else None

    if repo_root:
        status_timeout = budget.child_timeout(10) if budget is not None else 10
        if status_timeout <= 0:
            parts.append(f"repo git: {repo_root}")
            return "\n\n".join(parts), evidence_execution, True
        status_result = run_subprocess(
            "verifier",
                ["git", "status", "--short"], cwd=wd,
                timeout=status_timeout,
                execution_control=execution_control,
                service_profile="verification",
            )
        if status_result.execution is not None:
            evidence_execution = status_result.execution
            if _is_terminal_stop(status_result.execution):
                parts.append(f"repo git: {repo_root}")
                return "\n\n".join(parts), evidence_execution, status_result.timed_out
        evidence_timed_out = evidence_timed_out or status_result.timed_out
        status = (status_result.stdout or "").strip()
        diff_timeout = budget.child_timeout(10) if budget is not None else 10
        if diff_timeout <= 0:
            parts.append(f"repo git: {repo_root}")
            parts.append(f"git status --short:\n{status or '(limpo — nada modificado)'}")
            return "\n\n".join(parts), evidence_execution, True
        diffstat_result = run_subprocess(
            "verifier",
                ["git", "diff", "--stat", "HEAD"], cwd=wd,
                timeout=diff_timeout,
                execution_control=execution_control,
                service_profile="verification",
            )
        if diffstat_result.execution is not None:
            evidence_execution = diffstat_result.execution
            if _is_terminal_stop(diffstat_result.execution):
                parts.append(f"repo git: {repo_root}")
                parts.append(f"git status --short:\n{status or '(limpo — nada modificado)'}")
                return "\n\n".join(parts), evidence_execution, diffstat_result.timed_out
        evidence_timed_out = evidence_timed_out or diffstat_result.timed_out
        diffstat = (diffstat_result.stdout or "").strip()
        parts.append(f"repo git: {repo_root}")
        parts.append(f"git status --short:\n{status or '(limpo — nada modificado)'}")
        if diffstat_result.exit_code == 0:
            parts.append(f"git diff --stat HEAD:\n{diffstat or '(sem diff)'}")
        else:
            parts.append("git diff --stat HEAD:\n(sem diff ou HEAD indisponível)")
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

    return "\n\n".join(parts), evidence_execution, evidence_timed_out


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


def _parse_verdict(output: str) -> dict | None:
    """Extrai o JSON do veredito mesmo com texto em volta."""
    match = re.search(r"\{[^{}]*\"verdadeiro\"[^{}]*\}", output or "", re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        if isinstance(data.get("verdadeiro"), bool):
            return data
    except json.JSONDecodeError:
        return None
    return None


def pick_verifier(executor_provider: str) -> tuple | None:
    """Escolhe AUTONOMAMENTE o melhor verificador disponível na máquina.

    Regras, em ordem:
    1. Nunca o mesmo provider que executou a tarefa (anti-conluio).
    2. Grátis primeiro: modelos *-free do OpenCode e Ollama local.
    3. Desempate pela nota de 'rapidez' da tabela de ratings (barato/rápido).
    4. Se nada casar, cai na VERIFIER_CHAIN estática.
    """
    from athena.providers import PROVIDERS, list_providers, resolve_binary
    from athena.ratings import ratings_for_model

    candidates: list[tuple] = []  # (free, rapidez, provider_id, model_id)
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
    working_directory: str | None = None,
    executor_provider: str = "",
    execution_id: str | None = None,
    attempt_id: str | None = None,
    on_execution_update: Callable[[dict], None] | None = None,
    execution_control: ExecutionControl | None = None,
    verification_timeout_s: float = 600,
) -> Verdict:
    """Verifica um relatório. Retorna Verdict (verdadeiro=None se não deu pra verificar).

    Camadas, em ordem:
    1. DETERMINÍSTICA (sem modelo): re-roda testes/lint alegados e checa
       arquivos citados. Se for conclusiva, o veredito dela é final.
    2. ADVISORY (modelo barato): triagem para o que não tem oráculo
       automatizável. ATHENA_VERIFY_MODE=advisory|deterministic|auto.
    """
    if (
        isinstance(verification_timeout_s, bool)
        or float(verification_timeout_s) <= 0
        or not math.isfinite(float(verification_timeout_s))
    ):
        raise ValueError("verification_timeout_s deve ser número positivo")
    wd = working_directory or os.getcwd()
    mode = os.environ.get("ATHENA_VERIFY_MODE", "auto").lower()
    lifecycle_kwargs: dict[str, str] = {}
    if execution_id is not None:
        lifecycle_kwargs["execution_id"] = execution_id
    if attempt_id is not None:
        lifecycle_kwargs["attempt_id"] = attempt_id
    phase = ExecutionRecord(
        provider="verifier",
        profile="verification",
        on_update=on_execution_update,
        **lifecycle_kwargs,
    )
    phase.transition(ExecutionState.STARTING)
    phase.transition(ExecutionState.RUNNING)
    phase.configure_deadlines(absolute_deadline_s=float(verification_timeout_s))
    budget = DeadlineBudget(verification_timeout_s)

    def _terminate_with(state: ExecutionState, *, reason: str | None = None) -> dict:
        if execution_control is not None and execution_control.client_abandoned:
            phase.mark_client_abandoned()
        if state == ExecutionState.CANCELLED:
            phase.transition(ExecutionState.CANCELLATION_REQUESTED, reason=reason)
            phase.transition(ExecutionState.TERMINATING, reason=reason)
            phase.transition(ExecutionState.CANCELLED, reason=reason)
        elif state == ExecutionState.TERMINATION_UNCONFIRMED:
            phase.transition(ExecutionState.TERMINATING, reason=reason)
            phase.transition(ExecutionState.TERMINATION_UNCONFIRMED, reason=reason)
        else:
            phase.transition(state, reason=reason)
        return phase.to_dict()

    def _timed_out_execution() -> dict:
        return _terminate_with(
            ExecutionState.TIMED_OUT,
            reason="verification_deadline",
        )

    from athena.dverify import deterministic_verify

    try:
        if budget.expired:
            return Verdict(
                verdadeiro=None,
                confianca="baixa",
                motivos=["Orçamento da verificação expirou antes de iniciar os estágios."],
                verificador="deterministic",
                execution=_timed_out_execution(),
            )
        det = deterministic_verify(
            report,
            wd,
            budget=budget,
            execution_control=execution_control,
        )
        if det.termination_unconfirmed:
            return Verdict(
                verdadeiro=None,
                confianca="baixa",
                motivos=["Verificador determinístico com terminação indeterminada."],
                verificador="deterministic",
                execution=_terminate_with(ExecutionState.TERMINATION_UNCONFIRMED),
            )
        if det.deadline_exhausted:
            return Verdict(
                verdadeiro=None,
                confianca="baixa",
                motivos=["Orçamento da verificação esgotado durante etapa determinística."],
                verificador="deterministic",
                execution=_timed_out_execution(),
            )
        deterministic_cancelled = (
            (
                (getattr(det, "execution", None) or {}).get("state")
                == ExecutionState.CANCELLED.value
                and not bool(getattr(det, "timed_out", False))
            )
            or any(
                (check.execution or {}).get("state") == ExecutionState.CANCELLED.value
                and not check.timed_out
                for check in det.checks
            )
        )
        if deterministic_cancelled:
            return Verdict(
                verdadeiro=None,
                confianca="baixa",
                motivos=["Verificação cancelada por solicitação de cancelamento."],
                verificador="deterministic",
                execution=_terminate_with(
                    ExecutionState.CANCELLED,
                    reason=(execution_control.cancel_reason if execution_control else "user_requested"),
                ),
            )
        if det.verdadeiro is not None:
            det_payload = det.to_dict(
                include_check_execution=False,
                include_check_output_tail=False,
            )
            det_payload.pop("execution", None)
            return Verdict(
                verdadeiro=det.verdadeiro,
                confianca="alta",
                motivos=det.motivos,
                evidencias=json.dumps(det_payload, ensure_ascii=False),
                verificador="deterministic",
                execution=_terminate_with(ExecutionState.COMPLETED),
            )
        if mode == "deterministic":
            return Verdict(
                verdadeiro=None,
                motivos=["Nenhuma alegação verificável deterministicamente no relatório."],
                verificador="deterministic",
                execution=_terminate_with(ExecutionState.COMPLETED),
            )
        if budget.expired:
            return Verdict(
                verdadeiro=None,
                confianca="baixa",
                motivos=["Orçamento da verificação expirou antes da etapa advisory."],
                verificador="deterministic",
                execution=_timed_out_execution(),
            )

        chosen = pick_verifier(executor_provider)
        if not chosen:
            return Verdict(
                verdadeiro=None,
                motivos=["Nenhum verificador disponível."],
                execution=_terminate_with(ExecutionState.COMPLETED),
            )

        provider_id, model = chosen
        evidence, evidence_execution, evidence_timed_out = collect_evidence(
            working_directory,
            report,
            budget=budget,
            execution_control=execution_control,
        )
        if (evidence_execution or {}).get("state") == ExecutionState.TERMINATION_UNCONFIRMED.value:
            return Verdict(
                verdadeiro=None,
                motivos=["Coleta de evidências terminou com terminação indeterminada."],
                verificador="deterministic",
                execution=_terminate_with(ExecutionState.TERMINATION_UNCONFIRMED),
            )
        if evidence_timed_out:
            return Verdict(
                verdadeiro=None,
                confianca="baixa",
                motivos=["Orçamento da verificação esgotado durante a coleta de evidências."],
                verificador="deterministic",
                execution=_timed_out_execution(),
            )
        if (evidence_execution or {}).get("state") == ExecutionState.CANCELLED.value:
            return Verdict(
                verdadeiro=None,
                motivos=["Coleta de evidências cancelada."],
                verificador="deterministic",
                execution=_terminate_with(
                    ExecutionState.CANCELLED,
                    reason=(execution_control.cancel_reason if execution_control else "user_requested"),
                ),
            )
        prompt = _VERIFY_PROMPT.format(task=task_prompt, report=report, evidence=evidence)

        # Import tardio para evitar ciclo providers ↔ verifier
        from athena.providers import ask_provider

        advisory_timeout = budget.child_timeout(min(120.0, budget.remaining))
        if advisory_timeout <= 0:
            return Verdict(
                verdadeiro=None,
                confianca="baixa",
                motivos=["Orçamento da verificação expirou antes da chamada advisory."],
                verificador="deterministic",
                execution=_timed_out_execution(),
            )
        result = ask_provider(
            provider_id,
            prompt,
            use_default_role=False,
            model=model,
            working_directory=working_directory,
            timeout=advisory_timeout,
            service_profile="verification",
            with_contract=False,
            execution_id=phase.execution_id,
            attempt_id=phase.attempt_id,
            execution_control=execution_control,
        )
        if (result.execution or {}).get("state") == ExecutionState.TERMINATION_UNCONFIRMED.value:
            return Verdict(
                verdadeiro=None,
                motivos=["Verificador advisory terminou com terminação indeterminada."],
                evidencias=evidence,
                verificador=f"{provider_id}/{model or 'default'}",
                execution=_terminate_with(ExecutionState.TERMINATION_UNCONFIRMED),
            )
        if result.timed_out:
            return Verdict(
                verdadeiro=None,
                motivos=["Orçamento da verificação expirou na chamada advisory."],
                evidencias=evidence,
                verificador=f"{provider_id}/{model or 'default'}",
                execution=_timed_out_execution(),
            )
        if (result.execution or {}).get("state") == ExecutionState.CANCELLED.value:
            return Verdict(
                verdadeiro=None,
                motivos=["Verificador advisory cancelado."],
                evidencias=evidence,
                verificador=f"{provider_id}/{model or 'default'}",
                execution=_terminate_with(
                    ExecutionState.CANCELLED,
                    reason=(execution_control.cancel_reason if execution_control else "user_requested"),
                ),
            )
        parsed = _parse_verdict(result.output)
        if parsed is None:
            return Verdict(
                verdadeiro=None,
                motivos=[f"Verificador {provider_id} não retornou JSON válido."],
                evidencias=evidence,
                verificador=f"{provider_id}/{model or 'default'}",
                execution=_terminate_with(ExecutionState.COMPLETED),
            )
        return Verdict(
            verdadeiro=parsed["verdadeiro"],
            confianca=str(parsed.get("confianca", "baixa")),
            motivos=[str(m) for m in parsed.get("motivos", [])][:5],
            evidencias=evidence,
            verificador=f"{provider_id}/{model or 'default'}",
            execution=_terminate_with(ExecutionState.COMPLETED),
        )
    except Exception as exc:
        return Verdict(
            verdadeiro=None,
            motivos=[f"Erro interno do verificador: {exc}"],
            execution=_terminate_with(
                ExecutionState.FAILED,
                reason="internal_verifier_error",
            ),
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
