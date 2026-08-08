from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from athena.agents import (
    DEFAULT_PROVIDER_ROLES,
    apply_role,
    get_role,
    resolve_roles_for_providers,
)
from athena.bridge import RunResult, _enriched_env, run_subprocess, run_with_pty, which
from athena.contract import apply_contract, check_report_format
from athena.models import (
    catalog_meta,
    get_recommended_default_model,
    legacy_catalog_for_provider,
    model_in_catalog,
    resolve_model,
)
from athena.models import (
    is_heavy_model as catalog_is_heavy,
)
from athena.selfdetect import detect_self_provider
from athena.ssh import build_ssh_command
from athena.usage import record_usage

DEFAULT_COUNCIL: tuple[str, ...] = ("codex", "agent", "claude")

# --- Scanner Inteligente de CLIs no PATH ---------------------------------

# Palavras-chave que indicam CLI de IA (usado no nome do binário)
_AI_KEYWORDS = frozenset([
    "ai", "llm", "gpt", "claude", "codex", "copilot", "agent", "code",
    "gemini", "openai", "anthropic", "cursor", "groq", "ollama",
    "mistral", "perplexity", "aider", "continue", "kimi", "agy",
    "openclaude", "mods", "sgpt", "fabric", "iceberg", "opencode",
    "pencode", "repomix", "repoprompt", "chat", "prompt",
    "goose", "crush", "qwen", "cagent", "aichat", "windsurf",
    "auggie", "codebuddy", "plandex", "chatgpt", "cody",
])

# Palavras-chave na saída de --version / --help que confirmam CLI de IA
_AI_HELP_KEYWORDS = frozenset([
    "llm", "gpt", "claude", "openai", "anthropic", "codex", "model",
    "prompt", "chat", "completion", "generate", "ai", "agent",
    "gemini", "google", "cursor", "copilot", "ollama", "mistral",
])

# Binários que NÃO são CLIs de IA mas podem ter nomes parecidos
_BLOCKLIST = frozenset([
    "mail", "wait", "cal", "cat", "cut", "git", "log", "man", "top",
    "ps", "ls", "cd", "cp", "mv", "rm", "df", "du", "dd", "ed",
    "ex", "vi", "vim", "emacs", "nano", "less", "more", "head",
    "tail", "grep", "sed", "awk", "tar", "zip", "unzip", "ssh",
    "scp", "curl", "wget", "ping", "traceroute", "netstat", "lsof",
    "psql", "mysql", "sqlite3", "redis-cli", "mongosh", "node",
    "python", "python3", "ruby", "perl", "php", "java", "javac",
    "go", "rustc", "cargo", "npm", "yarn", "pnpm", "pip", "pip3",
    "docker", "kubectl", "helm", "terraform", "ansible", "vagrant",
    "aws", "gcloud", "az", "firebase", "supabase", "vercel", "netlify",
    "gh", "git", "svn", "hg", "bzr", "cvs", "darcs", "fossil",
    "code", "xcodebuild", "cairo-trace", "agentxtrap", "encode_keychange",
])


def _looks_like_ai_cli(name: str) -> bool:
    """Heurística: o nome do binário parece ser uma CLI de IA?"""
    name_lower = name.lower()
    # No Windows, remove extensões de executável (claude.cmd -> claude)
    if sys.platform == "win32":
        name_lower = re.sub(r"\.(exe|cmd|bat|ps1|com)$", "", name_lower)
    if name_lower in _BLOCKLIST:
        return False
    # Match de palavra inteira: "agent" matcha "agent" mas NÃO "agentxtrap"
    parts = name_lower.replace("-", " ").replace("_", " ").split()
    for part in parts:
        if part in _AI_KEYWORDS:
            return True
    return False


def _probe_cli(binary_path: str) -> bool:
    """
    Tenta executar o binário com --help e verifica se a saída
    contém palavras-chave de IA. Retorna True se parece ser CLI de IA.
    No Windows, shims .cmd/.bat (npm CLIs) precisam do cmd.exe.
    """
    def _run_probe(args: list[str]) -> subprocess.CompletedProcess:
        if sys.platform == "win32" and str(args[0]).lower().endswith((".cmd", ".bat")):
            args = ["cmd", "/c"] + args
        return subprocess.run(args, capture_output=True, text=True, timeout=3)

    try:
        # Primeiro tenta --help
        result = _run_probe([binary_path, "--help"])
        output = (result.stdout + result.stderr).lower()
    except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError, OSError):
        return False

    # Se --help falhar, tenta --version
    if result.returncode != 0 or not output:
        try:
            result = _run_probe([binary_path, "--version"])
            output = (result.stdout + result.stderr).lower()
        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError, OSError):
            return False

    score = sum(1 for kw in _AI_HELP_KEYWORDS if kw in output)
    return score >= 3  # Pelo menos 3 palavras-chave para confirmar


def _get_all_binaries_in_path() -> list[str]:
    """Retorna todos os nomes de binários únicos no PATH enriquecido
    (PATH do processo + diretórios comuns por OS: ~/.local/bin, Homebrew,
    npm global, Scoop, cargo etc.). No Windows, deduplica por nome base
    sem extensão (claude.exe/claude.cmd contam como um só)."""
    path_dirs = _enriched_env().get("PATH", "").split(os.pathsep)
    seen = set()
    binaries = []
    for directory in path_dirs:
        if not directory:
            continue
        try:
            for entry in os.listdir(directory):
                key = entry.lower()
                if sys.platform == "win32":
                    key = re.sub(r"\.(exe|cmd|bat|ps1|com)$", "", key)
                if key not in seen:
                    seen.add(key)
                    binaries.append(entry)
        except (OSError, PermissionError):
            continue
    return binaries


def scan_path_for_clis() -> dict[str, ProviderSpec]:
    """
    Escaneia o PATH procurando por CLIs de IA.
    Primeiro filtra por nome (heurística), depois confirma com --help/--version.
    Retorna um dict de provider_id → ProviderSpec.
    """
    found: dict[str, ProviderSpec] = {}
    all_binaries = _get_all_binaries_in_path()

    def _base_name(name: str) -> str:
        """Nome do provider sem extensão de executável do Windows."""
        if sys.platform == "win32":
            return re.sub(r"\.(exe|cmd|bat|ps1|com)$", "", name.lower())
        return name

    # Fase 1: filtra por nome
    candidates = [name for name in all_binaries if _looks_like_ai_cli(name)]

    # Fase 2: confirma com --help (paralelo)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_probe_cli, which(name)): name for name in candidates if which(name)}
        for future in as_completed(futures):
            name = futures[future]
            pid = _base_name(name)
            try:
                if future.result() and pid not in found:
                    found[pid] = ProviderSpec(
                        id=pid,
                        name=pid.replace("-", " ").title(),
                        binary=name,
                        description=f"CLI de IA detectada automaticamente (`{name}`).",
                        default_timeout=300,
                        default_role=None,
                    )
            except Exception:
                continue

    return found


# --- Custom providers (override/manual) ---------------------------------

def load_custom_providers() -> dict[str, ProviderSpec]:
    """
    Carrega providers customizados do arquivo ~/.athena/custom_providers.json.
    Formato: [{"id": "...", "name": "...", "binary": "...", "description": "...", "default_timeout": 300}]
    Estes OVERRIDE o auto-detectado.
    """
    from athena.config import DATA_DIR
    custom_file = DATA_DIR / "custom_providers.json"
    found: dict[str, ProviderSpec] = {}
    if not custom_file.exists():
        return found
    try:
        with open(custom_file) as f:
            items = json.load(f)
        for item in items:
            pid = item.get("id")
            if not pid or not item.get("binary"):
                continue
            found[pid] = ProviderSpec(
                id=pid,
                name=item.get("name", pid),
                binary=item["binary"],
                description=item.get("description", f"Provider customizado `{pid}`."),
                default_timeout=item.get("default_timeout", 300),
                default_role=item.get("default_role"),
            )
    except Exception:
        pass
    return found


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    name: str
    binary: str
    description: str
    binary_candidates: tuple[str, ...] = ()
    requires_pty: bool = False
    default_timeout: int = 300
    default_role: str | None = None


PROVIDERS: dict[str, ProviderSpec] = {
    "codex": ProviderSpec(
        id="codex",
        name="Codex",
        binary="codex",
        description="OpenAI Codex CLI (`codex exec`).",
        requires_pty=False,
        default_timeout=300,
        default_role=DEFAULT_PROVIDER_ROLES["codex"],
    ),
    "agent": ProviderSpec(
        id="agent",
        name="Cursor",
        binary="agent",
        binary_candidates=("agent", "cursor-agent"),
        description="Cursor Agent CLI (`agent -p` / `cursor-agent -p`).",
        requires_pty=False,
        default_timeout=300,
        default_role=DEFAULT_PROVIDER_ROLES["agent"],
    ),
    "claude": ProviderSpec(
        id="claude",
        name="Claude Code",
        binary="claude",
        description="Anthropic Claude Code CLI (`claude -p`).",
        requires_pty=False,
        default_timeout=300,
        default_role=DEFAULT_PROVIDER_ROLES["claude"],
    ),
    "agy": ProviderSpec(
        id="agy",
        name="Antigravity CLI (Gemini)",
        binary="agy",
        description="Google Antigravity CLI — sucessor do Gemini CLI (`agy -p`).",
        requires_pty=False,
        default_timeout=120,
        default_role=DEFAULT_PROVIDER_ROLES["agy"],
    ),
    "openclaude": ProviderSpec(
        id="openclaude",
        name="OpenClaude",
        binary="openclaude",
        description="OpenClaude CLI (`openclaude -p` com saída JSON). Opt-in no Athena padrão.",
        requires_pty=False,
        default_timeout=300,
        default_role=DEFAULT_PROVIDER_ROLES["openclaude"],
    ),
    # --- Catálogo de CLIs de IA conhecidas --------------------------------
    # Todas aparecem no dashboard mesmo quando NÃO instaladas (status Offline),
    # para o usuário ver o que o Athena suporta e instalar se quiser.
    # Obs: o Gemini CLI (`gemini`) foi descontinuado pelo Google — o sucessor
    # é o Antigravity CLI (`agy`), já listado acima. A palavra-chave "gemini"
    # continua no scanner, então instalações antigas ainda são auto-detectadas.
    "kimi": ProviderSpec(
        id="kimi",
        name="Kimi CLI",
        binary="kimi",
        description="Moonshot Kimi CLI (`kimi -p`).",
        default_timeout=300,
    ),
    "ollama": ProviderSpec(
        id="ollama",
        name="Ollama",
        binary="ollama",
        description="Ollama — modelos locais (`ollama run <modelo>`).",
        default_timeout=300,
    ),
    "aider": ProviderSpec(
        id="aider",
        name="Aider",
        binary="aider",
        description="Aider — pair programming no terminal (`aider --message`).",
        default_timeout=300,
    ),
    "opencode": ProviderSpec(
        id="opencode",
        name="OpenCode",
        binary="opencode",
        description="OpenCode CLI (`opencode run`).",
        default_timeout=300,
    ),
    "goose": ProviderSpec(
        id="goose",
        name="Goose",
        binary="goose",
        description="Block Goose — agente de IA local (`goose run`).",
        default_timeout=300,
    ),
    "crush": ProviderSpec(
        id="crush",
        name="Crush",
        binary="crush",
        description="Charm Crush — agente de IA no terminal.",
        default_timeout=300,
    ),
    "qwen": ProviderSpec(
        id="qwen",
        name="Qwen Code",
        binary="qwen",
        binary_candidates=("qwen", "qwen-code"),
        description="Alibaba Qwen Code CLI (`qwen -p`).",
        default_timeout=300,
    ),
    "cagent": ProviderSpec(
        id="cagent",
        name="cagent (Docker)",
        binary="cagent",
        description="Docker cagent — runtime de agentes (`cagent run`).",
        default_timeout=300,
    ),
    "mods": ProviderSpec(
        id="mods",
        name="Mods",
        binary="mods",
        description="Charm Mods — IA em pipelines de shell (`mods`).",
        default_timeout=120,
    ),
    "aichat": ProviderSpec(
        id="aichat",
        name="AIChat",
        binary="aichat",
        description="AIChat — CLI all-in-one para LLMs (`aichat`).",
        default_timeout=120,
    ),
    "llm": ProviderSpec(
        id="llm",
        name="LLM (Simon Willison)",
        binary="llm",
        description="LLM CLI do Simon Willison (`llm \"prompt\"`).",
        default_timeout=120,
    ),
    "sgpt": ProviderSpec(
        id="sgpt",
        name="ShellGPT",
        binary="sgpt",
        description="ShellGPT — IA na linha de comando (`sgpt`).",
        default_timeout=120,
    ),
    "copilot": ProviderSpec(
        id="copilot",
        name="GitHub Copilot CLI",
        binary="copilot",
        binary_candidates=("copilot", "github-copilot-cli"),
        description="GitHub Copilot CLI.",
        default_timeout=120,
    ),
    "plandex": ProviderSpec(
        id="plandex",
        name="Plandex",
        binary="plandex",
        description="Plandex — engenharia com IA para tarefas grandes.",
        default_timeout=300,
    ),
    "auggie": ProviderSpec(
        id="auggie",
        name="Auggie (Augment Code)",
        binary="auggie",
        description="Augment Code CLI (`auggie`).",
        default_timeout=300,
    ),
    "codebuddy": ProviderSpec(
        id="codebuddy",
        name="CodeBuddy",
        binary="codebuddy",
        description="Tencent CodeBuddy CLI.",
        default_timeout=300,
    ),
    "windsurf": ProviderSpec(
        id="windsurf",
        name="Windsurf CLI",
        binary="windsurf",
        description="Windsurf (Codeium) CLI.",
        default_timeout=300,
    ),
}

# --- Auto-discover no import --------------------------------------------

_AUTO_DISCOVERED = scan_path_for_clis()
_CUSTOM_PROVIDERS = load_custom_providers()

# Custom providers têm prioridade sobre auto-detectados
_ALL_DYNAMIC = {**_AUTO_DISCOVERED, **_CUSTOM_PROVIDERS}

if _ALL_DYNAMIC:
    for pid, spec in _ALL_DYNAMIC.items():
        if pid not in PROVIDERS:
            PROVIDERS[pid] = spec

PROVIDER_IDS: tuple[str, ...] = tuple(
    list(PROVIDERS.keys())
    + [pid for pid in _ALL_DYNAMIC if pid not in PROVIDERS]
)

# agy é recomendado só para frontend — chamadas com task_type diferente vêm com aviso.
PROVIDER_TASK_SCOPE: dict[str, str] = {
    "agy": "frontend",
}


def is_heavy_model(provider_id: str, model: str | None) -> bool:
    return catalog_is_heavy(provider_id, model)


def resolve_binary(spec: ProviderSpec) -> str | None:
    for candidate in spec.binary_candidates or (spec.binary,):
        path = which(candidate)
        if path:
            return path
    return None


def list_providers() -> list[dict]:
    items = []
    self_provider = detect_self_provider(PROVIDER_IDS)
    meta = catalog_meta()
    for provider_id in PROVIDER_IDS:
        spec = PROVIDERS[provider_id]
        path = resolve_binary(spec)
        role = get_role(spec.default_role)
        items.append({
            "id": spec.id,
            "name": spec.name,
            "binary": spec.binary,
            "binary_candidates": list(spec.binary_candidates or (spec.binary,)),
            "description": spec.description,
            "available": path is not None,
            "path": path,
            "requires_pty": spec.requires_pty,
            "default_role": spec.default_role,
            "role_name": role.name if role else None,
            "is_self": provider_id == self_provider,
            "models": legacy_catalog_for_provider(provider_id),
            "recommended_default_model": get_recommended_default_model(provider_id),
            "models_updated_at": meta.get("updated_at"),
            "models_stale": meta.get("stale"),
        })
    return items


def _build_command(
    spec: ProviderSpec,
    prompt: str,
    *,
    binary: str,
    model: str | None = None,
    working_directory: str | None = None,
    skip_permissions: bool = False,
    extra_args: Sequence[str] | None = None,
    prompt_via_stdin: bool = False,
    timeout: int | None = None,
) -> list[str]:
    extra = list(extra_args or [])
    cmd: list[str] = [binary]

    if spec.id == "agent":
        cmd.extend(["-p", prompt])
        if model:
            cmd.extend(["--model", model])
        if working_directory:
            cmd.extend(["--add-dir", working_directory])
        if skip_permissions:
            cmd.append("--trust")
        cmd.extend(extra)
        return cmd

    if spec.id == "agy":
        print_timeout = timeout or spec.default_timeout
        cmd.extend(["-p", prompt, "--print-timeout", f"{print_timeout}s"])
        if model:
            cmd.extend(["--model", model])
        if working_directory:
            cmd.extend(["--add-dir", working_directory])
        if skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        cmd.extend(extra)
        return cmd

    if spec.id == "claude":
        cmd.extend(["-p", prompt])
        if model:
            cmd.extend(["--model", model])
        if working_directory:
            cmd.extend(["--add-dir", working_directory])
        if skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        cmd.extend(extra)
        return cmd

    if spec.id == "openclaude":
        cmd.extend(["-p", prompt])
        if model:
            cmd.extend(["--model", model])
        if working_directory:
            cmd.extend(["--add-dir", working_directory])
        if skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        cmd.extend(extra)
        return cmd

    if spec.id == "opencode":
        cmd.append("run")
        if model:
            cmd.extend(["-m", model])
        cmd.extend(extra)
        cmd.append(prompt)
        return cmd

    if spec.id == "ollama":
        cmd.append("run")
        if model:
            cmd.append(model)
        cmd.extend(extra)
        cmd.append(prompt)
        return cmd

    if spec.id == "codex":
        cmd.append("exec")
        if model:
            cmd.extend(["-m", model])
        if skip_permissions:
            cmd.extend(["--sandbox", "workspace-write"])
        cmd.extend(extra)
        if prompt_via_stdin:
            cmd.append("-")
        else:
            cmd.append(prompt)
        return cmd

    cmd.extend(extra)
    cmd.append(prompt)
    return cmd


def ask_provider(
    provider_id: str,
    prompt: str,
    *,
    role: str | None = None,
    use_default_role: bool = True,
    model: str | None = None,
    working_directory: str | None = None,
    timeout: int | None = None,
    skip_permissions: bool = False,
    extra_args: Sequence[str] | None = None,
    heavy_model_authorized: bool = False,
    task_type: str | None = None,
    ssh_host: str | None = None,
    with_contract: bool = True,
) -> RunResult:
    spec = PROVIDERS.get(provider_id)
    if spec is None:
        return RunResult(
            provider=provider_id,
            command=[],
            output="",
            exit_code=2,
            error=f"Provider desconhecido: {provider_id}. Use list_providers.",
        )

    if ssh_host:
        binary = spec.binary
    else:
        binary = resolve_binary(spec)
        if binary is None:
            candidates = ", ".join(spec.binary_candidates or (spec.binary,))
            return RunResult(
                provider=provider_id,
                command=[spec.binary],
                output="",
                exit_code=127,
                error=f"CLI não encontrada no PATH (tente: {candidates}).",
            )

    if role is not None:
        effective_role = role
    elif use_default_role:
        effective_role = spec.default_role
    else:
        effective_role = None

    requested_model = model
    effective_model = resolve_model(provider_id, model)
    model_warning: str | None = None
    if (
        requested_model
        and effective_model != requested_model.strip()
        and requested_model.strip().lower() != "auto"
        and not model_in_catalog(provider_id, requested_model)
    ):
        model_warning = (
            f"Modelo '{requested_model}' não está no catálogo de {provider_id}; "
            f"usando '{effective_model or 'default da CLI'}'."
        )

    effective_prompt = apply_contract(apply_role(effective_role, prompt)) if with_contract else prompt

    codex_stdin = spec.id == "codex" and not ssh_host

    command = _build_command(
        spec,
        effective_prompt,
        binary=binary,
        model=effective_model,
        working_directory=working_directory,
        skip_permissions=skip_permissions,
        extra_args=extra_args,
        prompt_via_stdin=codex_stdin,
        timeout=timeout or spec.default_timeout,
    )
    effective_timeout = timeout or spec.default_timeout
    stdin_text = effective_prompt if codex_stdin else None

    if ssh_host:
        command = build_ssh_command(
            ssh_host,
            command,
            working_directory=working_directory,
            force_pty=spec.requires_pty,
        )
        result = run_subprocess(
            provider_id,
            command,
            cwd=None,
            timeout=effective_timeout,
            input_text=stdin_text,
        )
    elif spec.requires_pty:
        result = run_with_pty(
            provider_id,
            command,
            cwd=working_directory,
            timeout=effective_timeout,
        )
    else:
        result = run_subprocess(
            provider_id,
            command,
            cwd=working_directory,
            timeout=effective_timeout,
            input_text=stdin_text,
        )

    result.role = effective_role
    if model_warning:
        result.warnings.append(model_warning)
    if not result.timed_out and result.error is None:
        result.report_format_ok = check_report_format(result.output)

    if provider_id == detect_self_provider(PROVIDER_IDS):
        result.warnings.append(
            "Este provider foi detectado como o próprio CLI orquestrador "
            "(ATHENA_SELF_PROVIDER ou CLAUDECODE=1). Considere delegar a outro "
            "agente para evitar recursão/gasto redundante."
        )

    if is_heavy_model(provider_id, effective_model) and not heavy_model_authorized:
        result.warnings.append(
            f"Modelo '{effective_model}' é heavy no catálogo de {provider_id}. Chamado sem "
            "heavy_model_authorized=true — confirme com o usuário antes de escalar "
            "pra modelo pesado."
        )

    # Economia: modelo heavy/medium demais para tarefa simples desperdiça custo e latência
    if effective_model:
        from athena.recommend import estimate_complexity, recommend_for_task
        complexity = estimate_complexity(prompt)
        if complexity == "simple" and is_heavy_model(provider_id, effective_model):
            try:
                lighter = recommend_for_task(prompt, task_type=None, top_n=1)
                alt = lighter["recomendacoes"][0]["onde"][0] if lighter["recomendacoes"] else None
                tip = (
                    f" Alternativa mais barata: provider '{alt['provider']}' "
                    f"com model '{alt['model_id']}'." if alt else ""
                )
            except Exception:
                tip = ""
            result.warnings.append(
                f"A tarefa parece SIMPLES, mas o modelo '{effective_model}' é heavy — "
                f"custo e latência altos sem necessidade.{tip}"
            )

    required_scope = PROVIDER_TASK_SCOPE.get(provider_id)
    if required_scope and task_type and task_type != required_scope:
        result.warnings.append(
            f"Provider {provider_id} é recomendado só para tarefas de '{required_scope}' "
            f"— task_type informado foi '{task_type}'."
        )

    record_usage(
        provider_id,
        prompt_chars=len(effective_prompt),
        output_chars=len(result.output),
        duration_s=result.duration_s,
    )
    return result


def ask_provider_verified(
    provider_id: str,
    prompt: str,
    **kwargs,
) -> RunResult:
    """ask_provider + verificador ("detector de mentiras").

    Fluxo:
      1. Executa a tarefa no provider.
      2. Um modelo barato (escolhido autonomamente: grátis primeiro) compara
         o relatório com evidências do projeto (git, arquivos citados).
      3. FALSO → o executor recebe prompt de correção e tenta de novo.
      4. FALSO pela 2ª vez → escala: o resultado volta com
         verdict.escalado=True para o orquestrador decidir.
    """
    from athena.verifier import MAX_FIX_ATTEMPTS, build_fix_prompt, verify_report

    working_directory = kwargs.get("working_directory")
    result = ask_provider(provider_id, prompt, **kwargs)
    if result.error or result.timed_out:
        return result  # nada a verificar: a execução já falhou

    history: list[dict] = []
    for attempt in range(1, MAX_FIX_ATTEMPTS + 1):
        verdict = verify_report(
            prompt,
            result.output,
            working_directory=working_directory,
            executor_provider=provider_id,
        )
        verdict.tentativas = attempt
        history.append(verdict.to_dict())

        if verdict.verdadeiro is None:
            result.warnings.append(
                f"Verificação indisponível ({verdict.motivos[0] if verdict.motivos else 'erro'}) "
                "— relatório aceito sem checagem."
            )
            break
        if verdict.verdadeiro:
            result.verdict = {**verdict.to_dict(), "historico": history}
            return result

        if attempt < MAX_FIX_ATTEMPTS:
            result.warnings.append(
                f"Relatório marcado FALSO pelo verificador {verdict.verificador} "
                f"(tentativa {attempt}); reenviado ao executor para correção."
            )
            fix_prompt = build_fix_prompt(prompt, verdict)
            result = ask_provider(provider_id, fix_prompt, **kwargs)
            if result.error or result.timed_out:
                break
        else:
            verdict.escalado = True
            history[-1] = verdict.to_dict()
            result.verdict = {**verdict.to_dict(), "historico": history}
            result.warnings.append(
                "ESCALADO: relatório marcado FALSO 2 vezes pelo verificador. "
                "O orquestrador deve decidir: trocar de CLI, dividir a tarefa ou abortar. "
                f"Motivos: {'; '.join(verdict.motivos)}"
            )
            return result

    if result.verdict is None and history:
        result.verdict = {"verdadeiro": None, "historico": history}
    return result


def ask_provider_smart(
    provider_id: str,
    prompt: str,
    *,
    context_files: Sequence[str] | None = None,
    context_size_bytes: int = 0,
    role: str | None = None,
    use_default_role: bool = True,
    model: str | None = None,
    working_directory: str | None = None,
    timeout: int | None = None,
    skip_permissions: bool = False,
    extra_args: Sequence[str] | None = None,
    heavy_model_authorized: bool = False,
    task_type: str | None = None,
    ssh_host: str | None = None,
) -> RunResult:
    return ask_provider(
        provider_id,
        prompt,
        role=role,
        use_default_role=use_default_role,
        model=model,
        working_directory=working_directory,
        timeout=timeout,
        skip_permissions=skip_permissions,
        extra_args=extra_args,
        heavy_model_authorized=heavy_model_authorized,
        task_type=task_type,
        ssh_host=ssh_host,
    )


def deliberate(
    prompt: str,
    provider_ids: Sequence[str],
    *,
    roles: Sequence[str | None] | None = None,
    use_default_roles: bool = True,
    model: str | None = None,
    working_directory: str | None = None,
    timeout: int | None = None,
    skip_permissions: bool = False,
    extra_args: Sequence[str] | None = None,
    heavy_model_authorized: bool = False,
    task_type: str | None = None,
    ssh_host: str | None = None,
) -> list[RunResult]:
    unique_ids = []
    for provider_id in provider_ids:
        if provider_id not in unique_ids:
            unique_ids.append(provider_id)

    resolved_roles = resolve_roles_for_providers(
        unique_ids,
        roles,
        use_default_roles=use_default_roles,
    )

    results: list[RunResult] = []
    with ThreadPoolExecutor(max_workers=min(len(unique_ids), 4) or 1) as pool:
        futures = {
            pool.submit(
                ask_provider,
                provider_id,
                prompt,
                role=resolved_roles[index],
                use_default_role=False,
                model=model,
                working_directory=working_directory,
                timeout=timeout,
                skip_permissions=skip_permissions,
                extra_args=extra_args,
                heavy_model_authorized=heavy_model_authorized,
                task_type=task_type,
                ssh_host=ssh_host,
            ): provider_id
            for index, provider_id in enumerate(unique_ids)
        }
        for future in as_completed(futures):
            provider_id = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                results.append(
                    RunResult(
                        provider=provider_id,
                        command=[],
                        output="",
                        exit_code=1,
                        error=f"Falha interna no provider {provider_id}: {exc}",
                    )
                )

    order = {provider_id: index for index, provider_id in enumerate(unique_ids)}
    results.sort(key=lambda item: order.get(item.provider, 999))
    return results
