"""CFG-SEC-0: secure, atomic configuration snapshots."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

import athena.config_loader as loader
from athena.config_loader import (
    CONFIG_SCHEMA_VERSION,
    ConfigLoadError,
    ConfigSnapshotCache,
    build_manifest,
    load_config,
    validate_functions,
    validate_providers,
    write_snapshot,
)


@pytest.fixture()
def config_dir(tmp_path: Path) -> Path:
    return tmp_path / ".athena"


def _spec(**over):
    base = {
        "mode": "local",
        "runtime_class": "local",
        "base_url": "http://127.0.0.1:11434",
        "enabled": True,
        "approved": True,
    }
    base.update(over)
    return base


def _write_bundle(
    config_dir: Path,
    *,
    specialist: str = "context-condenser",
    version: str = "v1",
    document: dict | None = None,
) -> Path:
    bundle = config_dir / "personas" / specialist / version / "bundle.json"
    bundle.parent.mkdir(parents=True, exist_ok=True)
    bundle.write_text(
        json.dumps(
            document
            or {"specialist_id": specialist, "version": version, "prompt": "safe"}
        ),
        encoding="utf-8",
    )
    return bundle


def _write_parts(
    config_dir: Path,
    *,
    providers: dict | None = None,
    functions: dict | None = None,
    publish: bool = True,
) -> dict:
    config_dir.mkdir(parents=True, exist_ok=True)
    providers = providers or {
        "ollama": _spec(),
        "claude-cli": {
            "mode": "agent_cli",
            "runtime_class": "local",
            "command": "claude",
            "enabled": True,
            "approved": True,
        },
    }
    functions = functions or {
        "condensar-contexto": {
            "specialist": "context-condenser",
            "version": "v1",
            "min_status": "approved",
        }
    }
    (config_dir / "providers.json").write_text(
        json.dumps(providers), encoding="utf-8"
    )
    (config_dir / "functions.json").write_text(
        json.dumps(functions), encoding="utf-8"
    )
    _write_bundle(config_dir)
    manifest = build_manifest(config_dir)
    if publish:
        write_snapshot(config_dir, manifest)
    return manifest


def _raw_snapshot(config_dir: Path, manifest: dict | str) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    content = manifest if isinstance(manifest, str) else json.dumps(manifest)
    (config_dir / "snapshot.json").write_text(content, encoding="utf-8")


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def test_manifesto_valido_deterministico_inclui_persona(config_dir: Path):
    manifest = _write_parts(config_dir)

    assert manifest == build_manifest(config_dir)
    assert list(manifest["parts"]) == ["providers.json", "functions.json"]
    assert list(manifest["extras"]) == [
        "personas/context-condenser/v1/bundle.json"
    ]
    first_bytes = (config_dir / "snapshot.json").read_bytes()
    write_snapshot(config_dir, manifest)
    assert (config_dir / "snapshot.json").read_bytes() == first_bytes

    loaded = load_config(config_dir)
    assert loaded["schema_version"] == CONFIG_SCHEMA_VERSION
    assert loaded["personas"][
        "personas/context-condenser/v1/bundle.json"
    ]["version"] == "v1"


def test_hash_e_parse_usam_exatamente_os_mesmos_bytes(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    _write_parts(config_dir)
    providers_path = config_dir / "providers.json"
    original = providers_path.read_bytes()
    replacement = json.dumps({"attacker": _spec()}).encode()
    real_read = loader._read_regular_file
    reads = 0

    def racing_read(root: Path, name: str, limit: int) -> bytes:
        nonlocal reads
        content = real_read(root, name, limit)
        if name == "providers.json":
            reads += 1
            providers_path.write_bytes(replacement)
        return content

    monkeypatch.setattr(loader, "_read_regular_file", racing_read)
    loaded = load_config(config_dir)

    assert reads == 1
    assert loaded["providers"] == json.loads(original)
    assert providers_path.read_bytes() == replacement


def test_parte_obrigatoria_ausente_do_manifesto_recusa(config_dir: Path):
    manifest = _write_parts(config_dir, publish=False)
    del manifest["parts"]["functions.json"]
    _raw_snapshot(config_dir, manifest)

    with pytest.raises(ConfigLoadError, match="obrigatórias"):
        load_config(config_dir)


def test_bundle_referenciado_mas_nao_declarado_recusa(config_dir: Path):
    manifest = _write_parts(config_dir, publish=False)
    manifest["extras"] = {}
    _raw_snapshot(config_dir, manifest)

    with pytest.raises(ConfigLoadError, match="bundles"):
        load_config(config_dir)


def test_parte_extra_nao_persona_recusa(config_dir: Path):
    manifest = _write_parts(config_dir, publish=False)
    manifest["extras"]["other.json"] = "0" * 64
    _raw_snapshot(config_dir, manifest)

    with pytest.raises(ConfigLoadError, match="extra"):
        load_config(config_dir)


def test_hash_divergente_recusa(config_dir: Path):
    _write_parts(config_dir)
    (config_dir / "providers.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ConfigLoadError, match="hash divergente"):
        load_config(config_dir)


@pytest.mark.parametrize(
    "bad_path",
    [
        "/tmp/bundle.json",
        "personas/../escape/v1/bundle.json",
        r"personas\escape\v1\bundle.json",
        "",
        ".",
    ],
)
def test_caminho_nao_canonico_recusa(config_dir: Path, bad_path: str):
    manifest = _write_parts(config_dir, publish=False)
    manifest["extras"] = {bad_path: "0" * 64}
    _raw_snapshot(config_dir, manifest)

    with pytest.raises(ConfigLoadError, match="caminho|extra"):
        load_config(config_dir)


def test_caminhos_normalizados_colidentes_recusam(config_dir: Path):
    manifest = _write_parts(config_dir, publish=False)
    path = "personas/context-condenser/v1/bundle.json"
    manifest["extras"][path.upper()] = manifest["extras"][path]
    _raw_snapshot(config_dir, manifest)

    with pytest.raises(ConfigLoadError, match="colidem"):
        load_config(config_dir)


def test_symlink_escape_recusa(config_dir: Path, tmp_path: Path):
    manifest = _write_parts(config_dir, publish=False)
    outside = tmp_path / "outside.json"
    outside.write_text(
        '{"specialist_id":"context-condenser","version":"v1"}',
        encoding="utf-8",
    )
    bundle = config_dir / "personas/context-condenser/v1/bundle.json"
    bundle.unlink()
    bundle.symlink_to(outside)
    manifest["extras"][bundle.relative_to(config_dir).as_posix()] = _sha(
        outside.read_bytes()
    )
    _raw_snapshot(config_dir, manifest)

    with pytest.raises(ConfigLoadError):
        load_config(config_dir)


def test_alvo_nao_arquivo_recusa(config_dir: Path):
    manifest = _write_parts(config_dir, publish=False)
    bundle = config_dir / "personas/context-condenser/v1/bundle.json"
    bundle.unlink()
    bundle.mkdir()
    _raw_snapshot(config_dir, manifest)

    with pytest.raises(ConfigLoadError):
        load_config(config_dir)


def test_parte_fisica_ausente_recusa(config_dir: Path):
    _write_parts(config_dir)
    (config_dir / "functions.json").unlink()

    with pytest.raises(ConfigLoadError, match="ausente"):
        load_config(config_dir)


def test_json_com_chave_duplicada_recusa(config_dir: Path):
    _write_parts(config_dir)
    content = b'{"ollama":{"mode":"local"},"ollama":{"mode":"api"}}'
    (config_dir / "providers.json").write_bytes(content)
    manifest = build_manifest(config_dir)
    _raw_snapshot(config_dir, manifest)

    with pytest.raises(ConfigLoadError, match="duplicada"):
        load_config(config_dir)


def test_utf8_invalido_recusa(config_dir: Path):
    _write_parts(config_dir)
    (config_dir / "providers.json").write_bytes(b"\xff")
    manifest = build_manifest(config_dir)
    _raw_snapshot(config_dir, manifest)

    with pytest.raises(ConfigLoadError, match="UTF-8"):
        load_config(config_dir)


def test_tamanho_excessivo_recusa(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    _write_parts(config_dir)
    monkeypatch.setattr(loader, "MAX_PART_BYTES", 16)

    with pytest.raises(ConfigLoadError, match="tamanho"):
        load_config(config_dir)


def test_profundidade_excessiva_recusa(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    _write_parts(config_dir)
    bundle = config_dir / "personas/context-condenser/v1/bundle.json"
    nested: object = "end"
    for _ in range(8):
        nested = [nested]
    bundle.write_text(
        json.dumps(
            {
                "specialist_id": "context-condenser",
                "version": "v1",
                "nested": nested,
            }
        ),
        encoding="utf-8",
    )
    write_snapshot(config_dir, build_manifest(config_dir))
    monkeypatch.setattr(loader, "MAX_JSON_DEPTH", 6)

    with pytest.raises(ConfigLoadError, match="profundidade"):
        load_config(config_dir)


def test_contagem_excessiva_recusa(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    _write_parts(config_dir)
    bundle = config_dir / "personas/context-condenser/v1/bundle.json"
    bundle.write_text(
        json.dumps(
            {
                "specialist_id": "context-condenser",
                "version": "v1",
                "items": list(range(100)),
            }
        ),
        encoding="utf-8",
    )
    write_snapshot(config_dir, build_manifest(config_dir))
    monkeypatch.setattr(loader, "MAX_JSON_ITEMS", 50)

    with pytest.raises(ConfigLoadError, match="contagem"):
        load_config(config_dir)


@pytest.mark.parametrize(
    ("field", "wrong"),
    [("specialist_id", "other"), ("version", "v2")],
)
def test_bundle_com_identidade_divergente_recusa(
    config_dir: Path, field: str, wrong: str
):
    _write_parts(config_dir)
    bundle = config_dir / "personas/context-condenser/v1/bundle.json"
    document = json.loads(bundle.read_text(encoding="utf-8"))
    document[field] = wrong
    bundle.write_text(json.dumps(document), encoding="utf-8")
    _raw_snapshot(config_dir, build_manifest(config_dir))

    with pytest.raises(ConfigLoadError, match=field):
        load_config(config_dir)


@pytest.mark.parametrize(
    "secret_field",
    ["api_key", "API_KEY", "client_secret", "Client-Secret", "AUTHORIZATION"],
)
def test_campos_de_segredo_normalizados_sao_rejeitados(secret_field: str):
    marker = "sensitive-marker"
    with pytest.raises(ValueError, match="segredo") as error:
        validate_providers(
            {"api": {**_spec(mode="api"), secret_field: marker}}
        )

    assert marker not in str(error.value)


def test_authorization_aninhada_e_rejeitada_sem_vazar_valor():
    marker = "Bearer sensitive-marker"
    with pytest.raises(ValueError, match="segredo") as error:
        validate_providers(
            {"api": {**_spec(mode="api"), "headers": {"Authorization": marker}}}
        )

    assert marker not in str(error.value)


def test_base_url_com_userinfo_e_rejeitada_sem_vazar_valor():
    marker = "sensitive-marker"
    with pytest.raises(ValueError, match="credenciais") as error:
        validate_providers(
            {"api": _spec(mode="api", base_url=f"https://user:{marker}@example.test")}
        )

    assert marker not in str(error.value)


def test_base_url_com_query_de_segredo_e_rejeitada_sem_vazar_valor():
    marker = "sensitive-marker"
    with pytest.raises(ValueError, match="credenciais") as error:
        validate_providers(
            {
                "api": _spec(
                    mode="api",
                    base_url=f"https://example.test/v1?CLIENT_SECRET={marker}",
                )
            }
        )

    assert marker not in str(error.value)


def test_provider_rejeita_campo_desconhecido():
    with pytest.raises(ValueError, match="não suportado"):
        validate_providers({"api": {**_spec(mode="api"), "headers": {}}})


def test_functions_rejeitam_segredo_aninhado_e_campo_desconhecido():
    with pytest.raises(ValueError, match="segredo"):
        validate_functions(
            {
                "f": {
                    "specialist": "context-condenser",
                    "version": "v1",
                    "metadata": {"CLIENT_SECRET": "sensitive-marker"},
                }
            }
        )
    with pytest.raises(ValueError, match="não suportado"):
        validate_functions(
            {
                "f": {
                    "specialist": "context-condenser",
                    "version": "v1",
                    "metadata": {},
                }
            }
        )


def test_persona_rejeita_authorization_aninhada(config_dir: Path):
    _write_parts(config_dir)
    _write_bundle(
        config_dir,
        document={
            "specialist_id": "context-condenser",
            "version": "v1",
            "headers": {"authorization": "Bearer sensitive-marker"},
        },
    )
    _raw_snapshot(config_dir, build_manifest(config_dir))

    with pytest.raises(ConfigLoadError, match="segredo") as error:
        load_config(config_dir)

    assert "sensitive-marker" not in str(error.value)


def test_secret_ref_valido_e_permitido():

    validate_providers(
        {
            "api": {
                **_spec(mode="api", runtime_class="frontier"),
                "secret_ref": "keychain:athena-api",
            }
        }
    )


def test_publicacao_atomica_substitui_e_restringe_permissao(config_dir: Path):
    manifest = _write_parts(config_dir, publish=False)
    (config_dir / "snapshot.json").write_text("old", encoding="utf-8")

    write_snapshot(config_dir, manifest)

    assert json.loads((config_dir / "snapshot.json").read_text()) == manifest
    assert (config_dir / "snapshot.json").stat().st_mode & 0o777 == 0o600
    assert not list(config_dir.glob(".snapshot.json.*.tmp"))


def test_falha_de_replace_preserva_snapshot_e_remove_temporario(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    manifest = _write_parts(config_dir, publish=False)
    snapshot = config_dir / "snapshot.json"
    snapshot.write_bytes(b"old snapshot")

    def fail_replace(_source, _destination, **_kwargs):
        raise OSError("injected")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(ConfigLoadError, match="publicar"):
        write_snapshot(config_dir, manifest)

    assert snapshot.read_bytes() == b"old snapshot"
    assert not list(config_dir.glob(".snapshot.json.*.tmp"))


@pytest.mark.parametrize("invalid_part", ["providers", "functions", "persona"])
def test_publicacao_semanticamente_invalida_preserva_snapshot_anterior(
    config_dir: Path, invalid_part: str
):
    _write_parts(config_dir)
    snapshot = config_dir / "snapshot.json"
    previous = snapshot.read_bytes()

    if invalid_part == "providers":
        providers = json.loads((config_dir / "providers.json").read_text())
        providers["ollama"]["unknown"] = True
        (config_dir / "providers.json").write_text(json.dumps(providers))
    elif invalid_part == "functions":
        functions = json.loads((config_dir / "functions.json").read_text())
        functions["condensar-contexto"].pop("version")
        (config_dir / "functions.json").write_text(json.dumps(functions))
    else:
        _write_bundle(
            config_dir,
            document={"specialist_id": "other", "version": "v1"},
        )

    candidate = build_manifest(config_dir)
    with pytest.raises(ConfigLoadError):
        write_snapshot(config_dir, candidate)

    assert snapshot.read_bytes() == previous
    assert not list(config_dir.glob(".snapshot.json.*.tmp"))


@pytest.mark.parametrize("failure_point", ["fchmod", "fdopen"])
def test_falha_antes_de_fdopen_fecha_descritor_e_remove_temporario(
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
):
    manifest = _write_parts(config_dir, publish=False)
    real_open = os.open
    real_close = os.close
    real_fdopen = os.fdopen
    created_fds: list[int] = []
    closed_fds: list[int] = []

    def tracked_open(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        if flags & os.O_CREAT:
            created_fds.append(fd)
        return fd

    def tracked_close(fd):
        closed_fds.append(fd)
        return real_close(fd)

    monkeypatch.setattr(os, "open", tracked_open)
    monkeypatch.setattr(os, "close", tracked_close)
    if failure_point == "fchmod":
        monkeypatch.setattr(os, "fchmod", lambda _fd, _mode: (_ for _ in ()).throw(OSError()))
    else:
        def fail_write_fdopen(fd, mode):
            if mode == "wb":
                raise OSError("injected")
            return real_fdopen(fd, mode)

        monkeypatch.setattr(os, "fdopen", fail_write_fdopen)

    with pytest.raises(ConfigLoadError, match="publicar"):
        write_snapshot(config_dir, manifest)

    assert len(created_fds) == 1
    assert created_fds[0] in closed_fds
    assert not list(config_dir.glob(".snapshot.json.*.tmp"))


def test_erro_ao_criar_config_dir_e_sanitizado(config_dir: Path):
    source = config_dir / "source"
    manifest = _write_parts(source, publish=False)
    blocker = config_dir / "blocker"
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("not a directory")

    with pytest.raises(ConfigLoadError, match="publicar") as error:
        write_snapshot(blocker / "child", manifest)

    assert str(blocker) not in str(error.value)


def test_refresh_rele_e_verifica_partes(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    _write_parts(config_dir)
    real_read = loader._read_regular_file
    reads: list[str] = []

    def counted(root: Path, name: str, limit: int) -> bytes:
        reads.append(name)
        return real_read(root, name, limit)

    monkeypatch.setattr(loader, "_read_regular_file", counted)
    cache = ConfigSnapshotCache(config_dir)
    assert cache.refresh()["providers"]
    assert cache.refresh()["providers"]

    assert reads.count("snapshot.json") == 2
    assert reads.count("providers.json") == 2
    assert reads.count("functions.json") == 2
    assert reads.count("personas/context-condenser/v1/bundle.json") == 2


def test_refresh_invalido_preserva_current(config_dir: Path):
    _write_parts(config_dir)
    cache = ConfigSnapshotCache(config_dir)
    first = cache.refresh()
    (config_dir / "providers.json").write_text("{}", encoding="utf-8")
    _raw_snapshot(config_dir, build_manifest(config_dir))

    with pytest.raises(ConfigLoadError, match="providers"):
        cache.refresh()

    assert cache.current == first


def test_retorno_do_cache_nao_muta_current(config_dir: Path):
    _write_parts(config_dir)
    cache = ConfigSnapshotCache(config_dir)
    returned = cache.refresh()
    returned["providers"]["ollama"]["enabled"] = False
    returned["personas"].clear()

    assert cache.current is not None
    assert cache.current["providers"]["ollama"]["enabled"] is True
    assert cache.current["personas"]


def test_snapshot_ausente_recusa(config_dir: Path):
    with pytest.raises(ConfigLoadError, match="ausente"):
        load_config(config_dir)


def test_modo_invalido_recusa(config_dir: Path):
    manifest = _write_parts(
        config_dir,
        providers={"x": {"mode": "telepatia"}},
        publish=False,
    )
    _raw_snapshot(config_dir, manifest)

    with pytest.raises(ConfigLoadError, match="mode deve ser"):
        load_config(config_dir)
