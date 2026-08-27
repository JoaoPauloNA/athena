"""CFG-4: modo de execução `agent_cli` resolvido como dado.

`resolve_execution_command(provider_spec, task_prompt, *, unattended)` traduz
a declaração declarativa de um provider (`mode: agent_cli`) em um RunRequest
do bridge — sem que o chamador saiba a sintaxe da CLI.

Fecha o defeito de empacotamento reportado para o Aletheia: quem instala
do GitHub passa a importar este módulo do pacote publicado.
"""

from __future__ import annotations

from typing import Any

from .bridge.contracts import RunRequest

# Flags "unattended" conhecidas por CLI. O modo declarado e auditável
# substitui flags soltas: a configuração concede, o Aegis pode negar.
_UNATTENDED_FLAGS = {
    "claude": ("--dangerously-skip-permissions",),
    "codex": ("--full-auto",),
    "default": ("--yes",),
}


def resolve_execution_command(spec: dict[str, Any], prompt: str, *,
                              unattended: bool = False,
                              cwd: str = "/tmp") -> RunRequest:
    """Construir RunRequest a partir da especificação do provider."""
    mode = spec.get("mode")
    if mode != "agent_cli":
        raise ValueError(f"modo '{mode}' não é agent_cli")
    argv0 = str(spec.get("command") or "").strip()
    if not argv0:
        raise ValueError("agent_cli exige 'command'")

    args: list[str] = [argv0]
    # linguagem do executor: prompt via flag -p/--prompt com fallback stdin?
    # Decisão CFG-4: passar o prompt por argumento padrão da CLI alvo.
    prompt_flag = spec.get("prompt_flag", "-p")
    if prompt_flag:
        args += [prompt_flag, prompt]
    else:
        args.append(prompt)

    if unattended:
        for flag in _UNATTENDED_FLAGS.get(argv0.split("/")[-1],
                                           _UNATTENDED_FLAGS["default"]):
            args.append(flag)

    return RunRequest(command=tuple(args), cwd=cwd,
                      lease_timeout_s=spec.get("lease_timeout_s"))
