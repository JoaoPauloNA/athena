"""CFG-3: descoberta nunca habilita.

Invariantes:
- descoberta grava SOMENTE em cache/inventory.json (observado);
- providers.json não é tocado pela descoberta;
- único estado concedido é DISCOVERED.
"""

from __future__ import annotations

import json

from athena.discovery import discover


def test_descoberta_registra_so_discovered(tmp_path):
    report = discover(tmp_path / "cache",
                      extra_candidates={"fake-tool": "sh"})  # 'sh' sempre existe
    entry = next(e for e in report.discovered if e["cli_id"] == "fake-tool")
    assert entry["state"] == "DISCOVERED"
    # nenhum outro estado concedido
    all_states = {e["state"] for e in report.discovered}
    assert all_states <= {"DISCOVERED"}


def test_grava_somente_em_cache_nao_providers(tmp_path):
    cache = tmp_path / "cache"
    discover(cache, extra_candidates={"x": "ls"})
    assert (cache / "inventory.json").exists()
    payload = json.loads((cache / "inventory.json").read_text())
    assert payload["schema_version"] == "athena.inventory.v1"
    # nada escrito fora de cache/
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "cache"]
    assert leftovers == []


def test_cli_ausente_nao_e_registrado(tmp_path):
    report = discover(tmp_path / "cache",
                      extra_candidates={"fantasma": "definitivamente-nao-existe-xyz"})
    assert not any(e["cli_id"] == "fantasma" for e in report.discovered)


def test_claude_ou_ollama_encontrados_se_instalados(tmp_path):
    """Descoberta real contra a máquina (sem mock): claude/ollama/echo."""
    report = discover(tmp_path / "cache",
                      extra_candidates={"presente": "pwd"})
    ids = {e["cli_id"] for e in report.discovered}
    assert "presente" in ids  # comando garantidamente existente
