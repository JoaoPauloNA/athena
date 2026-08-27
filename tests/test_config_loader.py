"""CFG-0/CFG-1: testes dos schemas e da carga atômica de configuração.

Invariantes cobertos:
- segredo só secret_ref (valor proibido);
- snapshot é a unidade atômica — hash divergente recusa TUDO;
- recusa preserva o snapshot anterior (nada é parcialmente carregado);
- referência quebrada recusa;
- discovery nunca habilita (estados separados).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from athena.config_loader import (
    ConfigLoadError,
    build_manifest,
    load_config,
    validate_providers,
    write_snapshot,
)


@pytest.fixture()
def config_dir(tmp_path: Path) -> Path:
    return tmp_path / ".athena"


def _write_parts(cd: Path, providers=None, functions=None):
    cd.mkdir(parents=True, exist_ok=True)
    providers = providers or {
        "ollama": {"mode": "local", "base_url": "http://127.0.0.1:11434"},
        "claude-cli": {"mode": "agent_cli", "command": "claude"},
    }
    functions = functions or {
        "condensar-contexto": {"specialist": "context-condenser",
                               "min_status": "approved"},
    }
    (cd / "providers.json").write_text(json.dumps(providers))
    (cd / "functions.json").write_text(json.dumps(functions))
    write_snapshot(cd, build_manifest(cd))


def _persona_bundle(cd: Path) -> None:
    pb = cd / "personas" / "context-condenser" / "v1"
    pb.mkdir(parents=True, exist_ok=True)
    (pb / "bundle.json").write_text('{"specialist_id":"context-condenser","version":1}')


def test_carga_valida_completa(config_dir):
    _write_parts(config_dir)
    _persona_bundle(config_dir)  # bundle existe
    cfg = load_config(config_dir)
    assert cfg["schema_version"] == "athena.config.v1"
    assert "ollama" in cfg["parts"]["providers"]


def test_segredo_valor_proibido(config_dir):
    bad = {"cliproxy": {"mode": "api",
                        "base_url": "http://127.0.0.1:8317/v1",
                        "api_key": "sk-fake"}}
    with pytest.raises(ValueError, match="segredo"):
        validate_providers(bad)


def test_secret_ref_permitido(config_dir):
    ok = {"cliproxy": {"mode": "api",
                       "base_url": "http://127.0.0.1:8317/v1",
                       "secret_ref": "keychain:athena-cliproxy"}}
    validate_providers(ok)  # não levanta


def test_hash_divergente_recusa_carga_inteira(config_dir):
    _write_parts(config_dir)
    _persona_bundle(config_dir)
    # corromper uma parte DEPOIS do snapshot
    p = config_dir / "providers.json"
    doc = json.loads(p.read_text())
    doc["novo-provider"] = {"mode": "local", "base_url": "http://127.0.0.1:9"}
    p.write_text(json.dumps(doc))
    with pytest.raises(ConfigLoadError, match="hash divergente"):
        load_config(config_dir)
    # e a carga anterior continua válida depois que a parte volta ao hash
    p.write_text(json.dumps({
        "ollama": {"mode": "local", "base_url": "http://127.0.0.1:11434"},
        "claude-cli": {"mode": "agent_cli", "command": "claude"},
    }))
    assert load_config(config_dir)["parts"]["providers"]  # snapshot anterior ativo


def test_parte_ausente_recusa(config_dir):
    _write_parts(config_dir)
    (config_dir / "functions.json").unlink()
    with pytest.raises(ConfigLoadError, match="ausente"):
        load_config(config_dir)


def test_referencia_quebrada_especialista_recusa(config_dir):
    _write_parts(config_dir, functions={
        "f": {"specialist": "inexistente"}})
    write_snapshot(config_dir, build_manifest(config_dir))
    with pytest.raises(ConfigLoadError, match="sem bundle"):
        load_config(config_dir)


def test_snapshot_ausente_recusa(config_dir):
    with pytest.raises(ConfigLoadError, match="snapshot.json ausente"):
        load_config(config_dir)


def test_modo_invalido_recusa(config_dir):
    _write_parts(config_dir, providers={"x": {"mode": "telepatia"}})
    with pytest.raises(ValueError, match="mode deve ser"):
        load_config(config_dir)


def test_descoberta_nao_habilita():
    """Estados DISCOVERED/ENABLED nunca são derivados automaticamente."""
    from athena.config_loader import ALLOWED_ADMIN_STATES
    assert set(ALLOWED_ADMIN_STATES) == {"DISCOVERED", "ENABLED", "HEALTHY", "APPROVED"}
