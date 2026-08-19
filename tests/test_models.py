"""Testes de resolução de modelo (sem rede)."""
import athena.models as models


def _fake_claude_models():
    return [
        {"id": "haiku", "name": "Haiku", "weight": "light"},
        {"id": "sonnet", "name": "Sonnet", "weight": "medium"},
        {"id": "opus", "name": "Opus", "weight": "heavy"},
    ]


def test_resolve_model_none_uses_recommended_default(monkeypatch):
    monkeypatch.setattr(models, "get_provider_models", lambda _pid: _fake_claude_models())
    monkeypatch.setattr(models, "get_recommended_default_model", lambda _pid: "sonnet")

    assert models.resolve_model("claude", None) == "sonnet"


def test_resolve_model_auto_returns_none(monkeypatch):
    monkeypatch.setattr(models, "get_provider_models", lambda _pid: _fake_claude_models())
    monkeypatch.setattr(models, "get_recommended_default_model", lambda _pid: "sonnet")

    assert models.resolve_model("claude", "auto") is None
    assert models.resolve_model("claude", "  AUTO  ") is None


def test_resolve_model_known_id_returns_canonical_catalog_id(monkeypatch):
    monkeypatch.setattr(models, "get_provider_models", lambda _pid: _fake_claude_models())
    monkeypatch.setattr(models, "get_recommended_default_model", lambda _pid: "sonnet")

    assert models.resolve_model("claude", "sonnet") == "sonnet"
    assert models.resolve_model("claude", "SONNET") == "sonnet"


def test_resolve_model_known_display_name_returns_canonical_catalog_id(monkeypatch):
    monkeypatch.setattr(models, "get_provider_models", lambda _pid: _fake_claude_models())
    monkeypatch.setattr(models, "get_recommended_default_model", lambda _pid: "sonnet")

    assert models.resolve_model("claude", "Haiku") == "haiku"


def test_resolve_model_unknown_explicit_preserves_normalized_text(monkeypatch):
    monkeypatch.setattr(models, "get_provider_models", lambda _pid: _fake_claude_models())
    monkeypatch.setattr(models, "get_recommended_default_model", lambda _pid: "sonnet")

    assert models.resolve_model("qwen", "qwenproxy-3.8-max") == "qwenproxy-3.8-max"
    assert models.resolve_model("qwen", "  qwenproxy-3.8-max  ") == "qwenproxy-3.8-max"
    assert models.resolve_model("qwen", "qwenproxy-3.8-max") != "sonnet"
