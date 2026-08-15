from __future__ import annotations

import re
import shlex
from collections.abc import Sequence

_SHELL_META = set("'\"`$;&|<>!(){}*?\\")
_ALIAS_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_HOST_RE = re.compile(r"^[A-Za-z0-9.-]+$")
_IPV4_RE = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")
_BRACKETED_IPV6_RE = re.compile(r"^\[[0-9A-Fa-f:.]+\]$")
_HOSTNAME_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$")


def _valid_destination(value: str) -> bool:
    if not value or value.startswith("-"):
        return False
    if value.startswith(".") or value.endswith(".") or ".." in value:
        return False
    if _BRACKETED_IPV6_RE.match(value):
        return True
    if _IPV4_RE.match(value):
        return all(0 <= int(part) <= 255 for part in value.split("."))
    if _ALIAS_RE.match(value):
        return True
    if not _HOST_RE.match(value):
        return False
    labels = value.split(".")
    if any(not label for label in labels):
        return False
    return all(_HOSTNAME_LABEL_RE.match(label) for label in labels)


def _validate_ssh_host(ssh_host: str) -> str:
    host = ssh_host.strip()
    if not host:
        raise ValueError("ssh_host vazio não é permitido")
    if any(ch.isspace() for ch in host):
        raise ValueError("ssh_host não pode conter whitespace")
    if host.startswith("-"):
        raise ValueError("ssh_host não pode começar com '-'")
    if any(ch in _SHELL_META for ch in host):
        raise ValueError("ssh_host contém caracteres de shell não permitidos")
    if host.count("@") > 1:
        raise ValueError("ssh_host inválido: múltiplos '@'")

    user, destination = ("", host)
    if "@" in host:
        user, destination = host.split("@", 1)
        if not user or not _ALIAS_RE.match(user):
            raise ValueError("ssh_host inválido: usuário SSH inválido")
    if not _valid_destination(destination):
        raise ValueError("ssh_host inválido: destino deve ser alias, hostname ou IP")
    return host


def build_ssh_command(
    ssh_host: str,
    remote_argv: Sequence[str],
    *,
    working_directory: str | None = None,
    force_pty: bool = False,
) -> list[str]:
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
    host = _validate_ssh_host(ssh_host)
    quoted_cmd = " ".join(shlex.quote(part) for part in remote_argv)
    if working_directory:
        quoted_cmd = f"cd {shlex.quote(working_directory)} && {quoted_cmd}"

    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "NumberOfPasswordPrompts=0",
        "-o",
        "StrictHostKeyChecking=yes",
    ]
    if force_pty:
        cmd.append("-tt")
    cmd.extend(["--", host, quoted_cmd])
    return cmd
