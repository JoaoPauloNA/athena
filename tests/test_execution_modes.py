"""CFG-4: testes do modo agent_cli como dado de configuração.

Cobre: resolução declarativa, unattended auditável, rejeição de modos errados
e a prova do defeito real — importar do pacote publicado, não de legado/.
"""

from __future__ import annotations

import pytest

from athena.execution_modes import resolve_execution_command


def _spec(**over):
    base = {"mode": "agent_cli", "command": "claude"}
    base.update(over)
    return base


def test_resolucao_declarativa_basica():
    rr = resolve_execution_command(_spec(), "faça X")
    assert rr.command[0] == "claude"
    assert "-p" in rr.command and "faça X" in rr.command


def test_unattended_flag_por_cli():
    rr = resolve_execution_command(_spec(), "x", unattended=True)
    assert "--dangerously-skip-permissions" in rr.command  # flag claude

    rr2 = resolve_execution_command(_spec(command="desconhecida-cli"), "x",
                                    unattended=True)
    assert "--yes" in rr2.command  # fallback default


def test_modo_errado_rejeitado():
    with pytest.raises(ValueError, match="não é agent_cli"):
        resolve_execution_command({"mode": "api", "base_url": "http://x"}, "p")


def test_command_obrigatorio():
    with pytest.raises(ValueError, match="command"):
        resolve_execution_command({"mode": "agent_cli"}, "p")


def test_import_vem_do_pacote_nao_do_legado():
    """Prova da correção do defeito Aletheia: o módulo mora no pacote."""
    from pathlib import Path

    import athena.execution_modes as m
    p = Path(m.__file__)
    # dentro do checkout Athena-MCP, sob athena/ (não legado/)
    assert "/legado/" not in str(p)
    assert str(p).endswith("athena/execution_modes.py")


def test_runrequest_compativel_com_bridge():
    """O RunRequest produzido é aceito pelo contrato do bridge (dry)."""
    from dataclasses import fields as dfields
    rr = resolve_execution_command(_spec(), "tarefa")
    names = {f.name for f in dfields(type(rr))}
    assert {"command", "cwd"} <= names  # campos exigidos pelo bridge.run
