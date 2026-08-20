"""Construção pura de comandos SSH seguros e não interativos."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

from .contracts import SSHKeyAuthentication

_HOST_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._:-]*[A-Za-z0-9])?\Z")
_USERNAME_PATTERN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*\Z")


def _validate_host(host: str) -> str:
    if not isinstance(host, str) or not host:
        raise ValueError("SSH host must be a non-empty string")
    if host != host.strip() or any(character.isspace() for character in host):
        raise ValueError("SSH host must not contain whitespace")
    if "://" in host or host.startswith("-") or _HOST_PATTERN.fullmatch(host) is None:
        raise ValueError("SSH host contains an unsafe or unsupported value")
    return host


def _validate_username(username: str | None) -> str | None:
    if username is None:
        return None
    if not isinstance(username, str) or _USERNAME_PATTERN.fullmatch(username) is None:
        raise ValueError("SSH username contains an unsafe or unsupported value")
    return username


def _validate_port(port: int | None) -> int | None:
    if port is None:
        return None
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("SSH port must be an integer between 1 and 65535")
    return port


@dataclass(frozen=True, slots=True)
class SSHCommandBuilder:
    """Montar argv SSH sem iniciar processos nem acessar a rede."""

    authentication: SSHKeyAuthentication
    ssh_binary: str = "ssh"

    def __post_init__(self) -> None:
        if not isinstance(self.ssh_binary, str) or not self.ssh_binary.strip():
            raise ValueError("ssh_binary must be a non-empty string")

    def build(
        self,
        host: str,
        remote_command: str,
        *,
        username: str | None = None,
        port: int | None = None,
    ) -> tuple[str, ...]:
        """Retornar um argv com comando remoto protegido em um único token."""
        validated_host = _validate_host(host)
        validated_username = _validate_username(username)
        validated_port = _validate_port(port)
        if not isinstance(remote_command, str) or not remote_command.strip():
            raise ValueError("remote_command must be a non-empty string")
        if "\x00" in remote_command:
            raise ValueError("remote_command must not contain NUL bytes")

        target = (
            validated_host
            if validated_username is None
            else f"{validated_username}@{validated_host}"
        )
        argv = [
            self.ssh_binary,
            "-o",
            "BatchMode=yes",
            "-o",
            "PasswordAuthentication=no",
            "-o",
            "KbdInteractiveAuthentication=no",
            "-o",
            "ChallengeResponseAuthentication=no",
            "-o",
            "PreferredAuthentications=publickey",
            "-o",
            "IdentitiesOnly=yes",
            "-i",
            self.authentication.identity_file,
        ]
        if validated_port is not None:
            argv.extend(("-p", str(validated_port)))
        argv.extend((target, "sh", "-lc", shlex.quote(remote_command)))
        return tuple(argv)


def build_ssh_command(
    host: str,
    remote_command: str,
    *,
    identity_file: str,
    password: str | None = None,
    username: str | None = None,
    port: int | None = None,
) -> tuple[str, ...]:
    """Atalho funcional para construir argv SSH somente com chave."""
    authentication = SSHKeyAuthentication(
        identity_file=identity_file,
        password=password,
    )
    return SSHCommandBuilder(authentication).build(
        host,
        remote_command,
        username=username,
        port=port,
    )
