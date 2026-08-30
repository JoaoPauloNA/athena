"""CFG-0A: contrato de estados desejado/observado separados.

GATE 1 — casos GO obrigatórios:
- descoberto-sozinho rejeitado; enabled+unhealthy rejeitado;
- healthy sem approved rejeitado; approved desabilitado rejeitado;
- todos os requisitos → elegível; cache nunca muta configuração;
- mode inválido rejeitado; campo de valor de segredo rejeitado.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from athena.config_loader import (
    ConfigLoadError,
    build_manifest,
    load_config,
    load_observed,
    provider_eligible,
    validate_inventory,
    validate_provider_spec,
    write_snapshot,
)


def _spec(**over):
    base = {"mode": "local", "runtime_class": "local",
            "base_url": "http://127.0.0.1:11434",
            "enabled": True, "approved": True}
    base.update(over)
    return {k: v for k, v in base.items() if v is not ...}


def _write_parts(cd: Path, providers=None, functions=None):
    cd.mkdir(parents=True, exist_ok=True)
    (cd / "providers.json").write_text(json.dumps(providers or {
        "ollama": _spec()}))
    (cd / "functions.json").write_text(json.dumps(functions or {
        "condensar-contexto": {
            "specialist": "context-condenser", "version": "v1"}}))
    _persona_bundle(cd)
    write_snapshot(cd, build_manifest(cd))


def _persona_bundle(cd: Path):
    pb = cd / "personas" / "context-condenser" / "v1"
    pb.mkdir(parents=True, exist_ok=True)
    (pb / "bundle.json").write_text(
        '{"specialist_id":"context-condenser","version":"v1"}')


# --------------------------------------------------- estados administrativos

def test_descoberto_sozinho_rejeitado():
    ok, reason = provider_eligible(_spec(),
                                   {"discovered": True, "healthy": False})
    assert not ok and reason == "PROVIDER_UNHEALTHY"


def test_enabled_unhealthy_rejeitado():
    ok, reason = provider_eligible(_spec(enabled=True),
                                   {"discovered": True, "healthy": False})
    assert not ok and reason == "PROVIDER_UNHEALTHY"


def test_healthy_sem_approved_rejeitado():
    obs = {"discovered": True, "healthy": True}
    ok, reason = provider_eligible(_spec(approved=False), obs)
    assert not ok and reason == "PROVIDER_NOT_APPROVED"


def test_approved_desabilitado_rejeitado():
    obs = {"discovered": True, "healthy": True}
    ok, reason = provider_eligible(_spec(enabled=False), obs)
    assert not ok and reason == "PROVIDER_DISABLED"


def test_todos_requisitos_true_elegivel():
    obs = {"discovered": True, "healthy": True}
    ok, reason = provider_eligible(_spec(), obs)
    assert ok and reason is None


def test_aegis_negacao_vence_sempre():
    obs = {"discovered": True, "healthy": True}
    ok, reason = provider_eligible(_spec(), obs, aegis_allows=False)
    assert not ok and reason == "AEGIS_DENIED"


def test_sem_entrada_no_cache_falha_unhealthy():
    """Sem inventário = sem health = fail closed PROVIDER_UNHEALTHY."""
    ok, reason = provider_eligible(_spec(), None)  # nada observado
    assert not ok and reason == "PROVIDER_UNHEALTHY"


# ------------------------------------------------------- separação física

def test_cache_nunca_muta_config_desejada(tmp_path):
    cd = tmp_path / ".athena"
    _write_parts(cd)
    _persona_bundle(cd)
    before = (cd / "providers.json").read_text()
    # simular health check escrevendo só no cache
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "inventory.json").write_text(
        json.dumps({"entries": [{"provider_id": "ollama",
                                 "discovered": True, "healthy": False}]}))
    load_config(cd)  # carga continua com os MESMOS dados desejados
    assert (cd / "providers.json").read_text() == before
    cfg = load_config(cd)
    assert cfg["providers"]["ollama"]["enabled"] is True  # desejado intacto


def test_inventario_nao_pode_carregar_estado_administrativo():
    validate_inventory({"entries": [{"provider_id": "x",
                                     "discovered": True, "healthy": True}]})
    with pytest.raises(ValueError, match="administrativo"):
        validate_inventory({"entries": [{"provider_id": "x", "enabled": True}]})
    with pytest.raises(ValueError, match="administrativo"):
        validate_inventory({"enabled": False})  # também no nível raiz


def test_load_observed_vazio_quando_sem_cache(tmp_path):
    assert load_observed(tmp_path / "inexistente") == []


# --------------------------------------------------------------- validações

def test_mode_invalido_rejeitado():
    with pytest.raises(ValueError, match="mode deve ser"):
        validate_provider_spec("x", _spec(mode=...))


def test_runtime_class_e_obrigatorio_separado_do_mode():
    with pytest.raises(ValueError, match="runtime_class"):
        validate_provider_spec("x", _spec(runtime_class=...))
    # mistura: usar modo como runtime_class é rejeitado
    with pytest.raises(ValueError, match="runtime_class"):
        validate_provider_spec("x", _spec(runtime_class="agent_cli"))


def test_valor_de_segredo_rejeitado():
    for field in ("secret", "api_key", "token"):
        with pytest.raises(ValueError, match="segredo"):
            validate_provider_spec("cliproxy",
                                   _spec(mode="api", runtime_class="frontier",
                                         base_url="http://127.0.0.1:8317/v1",
                                         **{field: "valor-real"}))


def test_secret_ref_referencia_valida():
    spec = _spec(mode="api", runtime_class="frontier",
                 base_url="http://127.0.0.1:8317/v1",
                 secret_ref="keychain:athena-cliproxy")
    validate_provider_spec("cliproxy", spec)  # não levanta


def test_carga_divergente_recusa_inteira(tmp_path):
    cd = tmp_path / ".athena"
    _write_parts(cd)
    _persona_bundle(cd)
    p = cd / "providers.json"
    doc = json.loads(p.read_text())
    doc["novo"] = _spec()
    p.write_text(json.dumps(doc))
    with pytest.raises(ConfigLoadError, match="hash divergente"):
        load_config(cd)


def test_snapshot_ausente_recusa(tmp_path):
    with pytest.raises(ConfigLoadError, match="ausente"):
        load_config(tmp_path)
