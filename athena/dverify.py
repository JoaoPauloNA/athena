"""Verificador DETERMINÍSTICO de relatórios (claimed vs verified).

Diferente do verificador advisory (que usa um modelo barato para julgar o
relatório), este módulo NÃO usa nenhum modelo: ele re-executa exatamente o
que o relatório alega ter rodado (pytest, ruff, npm test, ...) e compara o
exit code real com o sucesso declarado, além de checar se os arquivos
citados como criados/editados realmente existem.

Regra de segurança:
- Apenas comandos de uma whitelist de verificação (testes/lint), token a
  token via shlex (nunca shell), com timeout e no máximo MAX_COMMANDS.
- Se o relatório ADMITE a falha perto do comando ("pytest falhou..."),
  o comando não é re-rodado; não há alegação positiva a confirmar.
- ATHENA_VERIFY_MODE=advisory desliga esta camada globalmente.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field

from athena.bridge import run_subprocess
from athena.execution import DeadlineBudget, ExecutionControl

# Comandos de verificação permitidos (primeiro token) → sem efeitos destrutivos.
_ALLOWED_BINS = {
    "pytest", "python", "python3", "coverage",
    "ruff", "mypy", "flake8", "pylint",
    "npm", "pnpm", "yarn", "npx", "vitest", "jest", "eslint", "tsc",
    "go", "cargo", "make",
}

MAX_COMMANDS = 3
COMMAND_TIMEOUT = 300  # segundos por comando

# Frases de comando de verificação que costumam aparecer em relatórios.
# Argumentos plausíveis de um comando: flags (-x, --noEmit) ou paths/alvos
# (tests/, a/b.py, modulo.alvo, x=y). Palavras soltas ("passou") não entram.
_ARG = r"(?:-{1,2}[\w-]+|[\w.]*[/:=][\w./:=\-]*|\d+)"

_CMD_RE = re.compile(
    r"(?P<cmd>"
    rf"(?:python3?\s+-m\s+)?pytest(?:\s+{_ARG})*"
    rf"|python3?\s+-m\s+unittest(?:\s+{_ARG})*"
    rf"|ruff\s+(?:check|format\s+--check)(?:\s+{_ARG})*"
    rf"|mypy(?:\s+{_ARG})*"
    r"|(?:npm|pnpm|yarn)\s+(?:run\s+)?(?:test|lint|typecheck|build)"
    rf"|npx\s+(?:vitest|jest|eslint|tsc)(?:\s+{_ARG})*"
    r"|vitest\s+run|jest"
    rf"|eslint(?:\s+{_ARG})+"
    r"|tsc\s+--noEmit"
    rf"|go\s+test(?:\s+{_ARG})*"
    rf"|cargo\s+test(?:\s+{_ARG})*"
    r"|make\s+test"
    r")",
    re.IGNORECASE,
)

_FILES_CLAIMED_RE = re.compile(
    r"[\w./-]+\.(?:py|js|ts|tsx|jsx|json|md|html|css|sql|yaml|yml|toml|txt)",
    re.IGNORECASE,
)

_FAILURE_WORDS_RE = re.compile(
    r"falh(?:ou|a|ando)|failed|failing|erro|error|não\s+passa|not\s+passing|quebrad",
    re.IGNORECASE,
)

_CREATED_WORDS_RE = re.compile(
    r"criad[oa]|criei|created|added|adicionei|adicionad[oa]|novo\s+arquivo|implement",
    re.IGNORECASE,
)


@dataclass
class CommandResult:
    command: str
    exit_code: int | None          # None = não executado (timeout/erro de OS)
    ok: bool
    output_tail: str = ""
    error: str = ""
    execution: dict | None = None
    termination_unconfirmed: bool = False
    timed_out: bool = False

    def to_dict(
        self,
        *,
        include_execution: bool = False,
        include_output_tail: bool = True,
    ) -> dict:
        payload = {
            "command": self.command,
            "exit_code": self.exit_code,
            "ok": self.ok,
            "error": self.error,
            "termination_unconfirmed": self.termination_unconfirmed,
            "timed_out": self.timed_out,
        }
        if include_output_tail:
            payload["output_tail"] = self.output_tail[-500:]
        if include_execution:
            payload["execution"] = self.execution
        return payload


@dataclass
class DeterministicVerdict:
    verdadeiro: bool | None        # None = nada conclusivo → cair no advisory
    motivos: list[str] = field(default_factory=list)
    checks: list[CommandResult] = field(default_factory=list)
    missing_files: list[str] = field(default_factory=list)
    execution: dict | None = None
    termination_unconfirmed: bool = False
    deadline_exhausted: bool = False
    timed_out: bool = False

    def to_dict(
        self,
        *,
        include_check_execution: bool = False,
        include_check_output_tail: bool = True,
    ) -> dict:
        return {
            "verdadeiro": self.verdadeiro,
            "motivos": self.motivos,
            "checks": [
                c.to_dict(
                    include_execution=include_check_execution,
                    include_output_tail=include_check_output_tail,
                )
                for c in self.checks
            ],
            "missing_files": self.missing_files,
            "execution": self.execution,
            "termination_unconfirmed": self.termination_unconfirmed,
            "deadline_exhausted": self.deadline_exhausted,
            "timed_out": self.timed_out,
        }


def _report_admits_failure(report: str, cmd_start: int, cmd_end: int) -> bool:
    """True se o texto PERTO da menção ao comando admite falha (relatório honesto)."""
    window = report[max(0, cmd_start - 200): cmd_end + 200]
    return bool(_FAILURE_WORDS_RE.search(window))


def extract_claimed_commands(report: str) -> list[str]:
    """Comandos de verificação que o relatório alega (sem admissão de falha)."""
    found: list[str] = []
    for match in _CMD_RE.finditer(report or ""):
        cmd = " ".join(match.group("cmd").split())
        if _report_admits_failure(report, match.start(), match.end()):
            continue  # relatório honesto sobre a falha — não é claimed success
        if cmd.lower() not in {c.lower() for c in found}:
            found.append(cmd)
    return found[:MAX_COMMANDS]


def _is_allowed(cmd: str) -> bool:
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return False
    if not tokens or tokens[0] not in _ALLOWED_BINS:
        return False
    # python -m pytest / go test / make test: segundo token precisa ser seguro.
    if tokens[0] in {"python", "python3"}:
        return len(tokens) >= 3 and tokens[1] == "-m" and tokens[2] in {"pytest", "unittest"}
    if tokens[0] == "go":
        return len(tokens) >= 2 and tokens[1] == "test"
    if tokens[0] == "cargo":
        return len(tokens) >= 2 and tokens[1] == "test"
    if tokens[0] == "make":
        return tokens[1:] == ["test"]
    if tokens[0] in {"npm", "pnpm", "yarn"}:
        joined = " ".join(tokens[1:])
        return any(k in joined for k in ("test", "lint", "typecheck", "build"))
    if tokens[0] == "npx":
        return len(tokens) >= 2 and tokens[1] in {"vitest", "jest", "eslint", "tsc"}
    return True


def run_command(
    cmd: str,
    working_directory: str,
    *,
    budget: DeadlineBudget | None = None,
    execution_control: ExecutionControl | None = None,
) -> CommandResult:
    """Executa um comando da whitelist e devolve o exit code real."""
    if budget is not None and budget.expired:
        return CommandResult(
            cmd,
            None,
            ok=False,
            error="verification_deadline",
            timed_out=True,
        )
    timeout_s = COMMAND_TIMEOUT
    if budget is not None:
        timeout_s = budget.child_timeout(COMMAND_TIMEOUT)
        if timeout_s <= 0:
            return CommandResult(
                cmd,
                None,
                ok=False,
                error="verification_deadline",
                timed_out=True,
            )
    result = run_subprocess(
        "verifier",
        shlex.split(cmd),
        cwd=working_directory,
        timeout=timeout_s,
        execution_control=execution_control,
        service_profile="verification",
    )
    tail = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    execution = result.execution
    state = (execution or {}).get("state")
    return CommandResult(
        cmd,
        result.exit_code,
        ok=result.exit_code == 0 and not result.timed_out,
        output_tail=tail,
        error=result.error or "",
        execution=execution,
        termination_unconfirmed=state == "TERMINATION_UNCONFIRMED",
        timed_out=result.timed_out,
    )


def _repo_root(
    working_directory: str,
    *,
    budget: DeadlineBudget | None = None,
    execution_control: ExecutionControl | None = None,
) -> tuple[str | None, dict | None, bool]:
    """Raiz do repo git que contém o wd (agentes costumam reportar paths
    relativos à raiz, não ao working_directory)."""
    if budget is not None and budget.expired:
        return None, None, True
    timeout_s = 10.0
    if budget is not None:
        timeout_s = budget.child_timeout(timeout_s)
        if timeout_s <= 0:
            return None, None, True
    top = run_subprocess(
        "verifier",
        ["git", "rev-parse", "--show-toplevel"],
        cwd=working_directory,
        timeout=timeout_s,
        execution_control=execution_control,
        service_profile="verification",
    )
    if top.exit_code == 0:
        return (top.stdout or "").strip(), top.execution, top.timed_out
    return None, top.execution, top.timed_out


def _file_exists(rel: str, working_directory: str, repo_root: str | None) -> bool:
    from pathlib import Path

    if (Path(working_directory) / rel).exists():
        return True
    if repo_root and (Path(repo_root) / rel).exists():
        return True
    return False


def find_missing_created_files(
    report: str,
    working_directory: str,
    *,
    budget: DeadlineBudget | None = None,
    execution_control: ExecutionControl | None = None,
) -> tuple[list[str], dict | None, bool]:
    """Arquivos citados como criados/editados que NÃO existem no disco.

    O path citado pode ser relativo ao working_directory OU à raiz do repo —
    os dois são aceitos antes de declarar o arquivo ausente.
    """
    repo_root, repo_root_execution, repo_root_timed_out = _repo_root(
        working_directory,
        budget=budget,
        execution_control=execution_control,
    )
    missing: list[str] = []
    for rel in sorted(set(_FILES_CLAIMED_RE.findall(report or "")))[:20]:
        # Só conta como alegação de criação se houver verbo de criação perto.
        idx = (report or "").find(rel)
        window = (report or "")[max(0, idx - 150): idx + len(rel) + 50]
        if not _CREATED_WORDS_RE.search(window):
            continue
        if rel.startswith(("http", "node_modules", ".venv")):
            continue
        if not _file_exists(rel, working_directory, repo_root):
            missing.append(rel)
    return missing, repo_root_execution, repo_root_timed_out


def deterministic_verify(
    report: str,
    working_directory: str,
    *,
    budget: DeadlineBudget | None = None,
    execution_control: ExecutionControl | None = None,
) -> DeterministicVerdict:
    """Verificação 100% determinística. None = inconclusivo (usar advisory)."""
    if os.environ.get("ATHENA_VERIFY_MODE", "auto").lower() == "advisory":
        return DeterministicVerdict(verdadeiro=None)

    verdict = DeterministicVerdict(verdadeiro=None)
    if budget is not None and budget.expired:
        verdict.deadline_exhausted = True
        return verdict
    contradictions: list[str] = []
    supports: list[str] = []

    # 1. Arquivos alegados como criados que não existem → contradição dura.
    missing, repo_root_execution, repo_root_timed_out = find_missing_created_files(
        report, working_directory, budget=budget, execution_control=execution_control
    )
    verdict.missing_files = missing
    if repo_root_execution is not None:
        verdict.execution = repo_root_execution
        state = repo_root_execution.get("state")
        if state == "TERMINATION_UNCONFIRMED":
            verdict.termination_unconfirmed = True
            return verdict
        if state == "CANCELLED":
            if repo_root_timed_out:
                verdict.deadline_exhausted = True
                verdict.timed_out = True
            return verdict
    if repo_root_timed_out:
        verdict.deadline_exhausted = True
        verdict.timed_out = True
        return verdict
    if missing:
        contradictions.append(
            "arquivos alegados como criados não existem: " + ", ".join(missing[:5])
        )

    # 2. Re-rodar comandos de verificação alegados e comparar exit codes.
    for cmd in extract_claimed_commands(report):
        if budget is not None and budget.expired:
            verdict.deadline_exhausted = True
            return verdict
        if not _is_allowed(cmd):
            continue
        result = run_command(
            cmd, working_directory, budget=budget, execution_control=execution_control
        )
        verdict.checks.append(result)
        if result.execution and verdict.execution is None:
            verdict.execution = result.execution
        if result.termination_unconfirmed:
            verdict.termination_unconfirmed = True
        if result.error:
            supports.append(f"'{cmd}' não pôde ser re-executado ({result.error})")
            if result.timed_out:
                verdict.deadline_exhausted = True
                verdict.timed_out = True
                return verdict
        elif result.ok:
            supports.append(f"'{cmd}' confirmado (exit 0)")
        else:
            contradictions.append(
                f"'{cmd}' alegado como passando, mas re-execução deu exit {result.exit_code}"
            )

    if contradictions:
        verdict.verdadeiro = False
        verdict.motivos = contradictions + supports
    elif verdict.checks and any(c.ok for c in verdict.checks):
        verdict.verdadeiro = True
        verdict.motivos = supports
    # Sem comandos nem contradições → None: cai no verificador advisory.
    return verdict
