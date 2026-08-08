from __future__ import annotations

import shlex
from typing import List, Optional, Sequence


def build_ssh_command(
    ssh_host: str,
    remote_argv: Sequence[str],
    *,
    working_directory: Optional[str] = None,
    force_pty: bool = False,
) -> List[str]:
    """Monta o comando `ssh` que executa `remote_argv` na máquina `ssh_host`.

    Cada token de `remote_argv` é escapado com `shlex.quote` e depois juntado numa única
    string, enviada como o (único) comando remoto. Isso evita que o conteúdo do prompt
    (aspas, backticks, `;`, `$(...)`) seja interpretado pelo shell remoto — sem isso, SSH
    concatena os argumentos crus e manda pro shell remoto interpretar, o que seria um vetor
    de injeção de comando via o próprio prompt da tarefa.

    `ssh_host` deve resolver via `~/.ssh/config` (alias) ou `user@host` — autenticação por
    chave já configurada (ssh-agent/known_hosts). O Athena não aceita nem manipula senha
    ou qualquer credencial.

    `force_pty` adiciona `-tt` (força alocação de PTY remoto mesmo com stdin local não-tty),
    necessário pra CLIs que exigem TTY (ex.: agy) — sem isso o `-t` implícito do ssh não é
    ativado porque rodamos com stdin=DEVNULL localmente.
    """
    quoted_cmd = " ".join(shlex.quote(part) for part in remote_argv)
    if working_directory:
        quoted_cmd = f"cd {shlex.quote(working_directory)} && {quoted_cmd}"

    cmd = ["ssh"]
    if force_pty:
        cmd.append("-tt")
    cmd.extend([ssh_host, "--", quoted_cmd])
    return cmd
