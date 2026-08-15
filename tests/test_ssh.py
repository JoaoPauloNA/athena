from __future__ import annotations

import pytest

from athena.ssh import build_ssh_command


@pytest.mark.parametrize(
    "host",
    [
        "my-alias",
        "host_01",
        "example.internal",
        "10.20.30.40",
        "[2001:db8::10]",
        "user@example.internal",
        "deploy@10.0.0.5",
        "root@[2001:db8::1]",
    ],
)
def test_build_ssh_command_accepts_safe_hosts(host):
    cmd = build_ssh_command(host, ["echo", "ok"])
    assert cmd[0] == "ssh"
    assert "--" in cmd
    idx = cmd.index("--")
    assert cmd[idx + 1] == host
    assert cmd[idx + 2] == "echo ok"
    assert "BatchMode=yes" in cmd
    assert "NumberOfPasswordPrompts=0" in cmd
    assert "StrictHostKeyChecking=yes" in cmd


@pytest.mark.parametrize(
    "host",
    [
        "",
        "   ",
        "-oProxyCommand=evil",
        "user@-oProxyCommand=evil",
        "user @host",
        "user@@host",
        "example.",
        "host;rm -rf /",
        "host$(id)",
        "host|cat",
    ],
)
def test_build_ssh_command_rejects_injection_or_invalid_hosts(host):
    with pytest.raises(ValueError):
        build_ssh_command(host, ["echo", "ok"])


def test_build_ssh_command_quotes_prompt_and_sets_noninteractive_options():
    cmd = build_ssh_command(
        "safe-host",
        ["python3", "-c", "print('x'); print(\"y\")"],
        working_directory="/tmp/my dir",
        force_pty=True,
    )
    assert "-tt" in cmd
    assert cmd.count("--") == 1
    remote_cmd = cmd[-1]
    assert "cd '/tmp/my dir' && " in remote_cmd
    assert "print('\"'\"'x'\"'\"')" in remote_cmd
