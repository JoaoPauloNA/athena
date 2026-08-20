"""Testes do transporte SSH modular e conservador."""

from __future__ import annotations

import ast
import shlex
from dataclasses import dataclass
from pathlib import Path

import pytest

from athena.execution import ExecutionState
from athena.transport import (
    RemoteExecutor,
    RemoteProcessOutcome,
    SSHCommandBuilder,
    SSHKeyAuthentication,
    build_ssh_command,
)


@dataclass
class FakeRunner:
    """Runner determinístico que nunca inicia processo nem acessa rede."""

    outcome: RemoteProcessOutcome | None = None
    raises_timeout: bool = False
    calls: list[tuple[tuple[str, ...], float | None]] | None = None

    def __post_init__(self) -> None:
        self.calls = []

    def run(
        self, argv: tuple[str, ...], *, timeout_s: float | None = None
    ) -> RemoteProcessOutcome:
        assert self.calls is not None
        self.calls.append((argv, timeout_s))
        if self.raises_timeout:
            raise TimeoutError("simulated timeout")
        assert self.outcome is not None
        return self.outcome


def _builder() -> SSHCommandBuilder:
    return SSHCommandBuilder(SSHKeyAuthentication("/fake/test_key"))


@pytest.mark.parametrize(
    "host",
    [
        "",
        " ",
        "host name",
        "host\tname",
        "host;whoami",
        "host|whoami",
        "host&whoami",
        "host$(whoami)",
        "ssh://example.test",
        "https://example.test",
        "-oProxyCommand=whoami",
    ],
)
def test_builder_rejects_unsafe_hosts(host: str) -> None:
    with pytest.raises(ValueError, match="SSH host"):
        _builder().build(host, "printf ok")


def test_remote_command_is_shell_quoted_as_one_argv_token() -> None:
    command = "printf '%s\\n' safe; touch /tmp/must-not-run && echo $HOME"

    argv = _builder().build("worker.example.test", command)

    assert argv[-3:] == ("sh", "-lc", shlex.quote(command))
    assert argv.count(shlex.quote(command)) == 1
    assert command not in argv


def test_builder_generates_key_only_noninteractive_options() -> None:
    argv = build_ssh_command(
        "192.0.2.10",
        "hostname",
        identity_file="/fake/id_ed25519",
        username="athena",
        port=2222,
    )

    assert argv[0] == "ssh"
    assert ("-i", "/fake/id_ed25519") == argv[argv.index("-i") : argv.index("-i") + 2]
    assert "BatchMode=yes" in argv
    assert "PasswordAuthentication=no" in argv
    assert "KbdInteractiveAuthentication=no" in argv
    assert "ChallengeResponseAuthentication=no" in argv
    assert "PreferredAuthentications=publickey" in argv
    assert "IdentitiesOnly=yes" in argv
    assert "password" not in " ".join(argv).lower().replace("passwordauthentication=no", "")
    assert "athena@192.0.2.10" in argv
    assert ("-p", "2222") == argv[argv.index("-p") : argv.index("-p") + 2]


def test_password_configuration_is_explicitly_rejected() -> None:
    with pytest.raises(ValueError, match="password authentication is not supported"):
        build_ssh_command(
            "worker.example.test",
            "hostname",
            identity_file="/fake/id_ed25519",
            password="never-accepted",
        )


def test_key_is_mandatory() -> None:
    with pytest.raises(ValueError, match="identity_file"):
        SSHKeyAuthentication("")


@pytest.mark.parametrize(
    ("outcome", "expected_return_code", "expected_timed_out"),
    [
        (RemoteProcessOutcome(0, stdout="ok"), 0, False),
        (RemoteProcessOutcome(23, stderr="failed"), 23, False),
        (RemoteProcessOutcome(None, timed_out=True), None, True),
    ],
    ids=("success", "failure", "timeout"),
)
def test_remote_outcomes_never_confirm_termination(
    outcome: RemoteProcessOutcome,
    expected_return_code: int | None,
    expected_timed_out: bool,
) -> None:
    runner = FakeRunner(outcome)
    argv = _builder().build("worker.example.test", "do-work")

    result = RemoteExecutor(runner).execute(argv, timeout_s=10)

    assert result.state is ExecutionState.TERMINATION_UNCONFIRMED
    assert result.return_code == expected_return_code
    assert result.timed_out is expected_timed_out
    assert runner.calls == [(argv, 10)]


def test_runner_timeout_exception_is_also_unconfirmed() -> None:
    runner = FakeRunner(raises_timeout=True)
    argv = _builder().build("worker.example.test", "do-work")

    result = RemoteExecutor(runner).execute(argv, timeout_s=1)

    assert result.state is ExecutionState.TERMINATION_UNCONFIRMED
    assert result.return_code is None
    assert result.timed_out is True


def test_transport_imports_only_execution_from_other_core_packages() -> None:
    package = Path(__file__).resolve().parents[1] / "athena" / "transport"

    imported_core_packages: set[str] = set()
    for module in package.glob("*.py"):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        absolute_imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module is not None
        }
        absolute_imports.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        imported_core_packages.update(
            name
            for name in absolute_imports
            if name == "athena" or name.startswith("athena.")
        )

    assert imported_core_packages == {"athena.execution"}
