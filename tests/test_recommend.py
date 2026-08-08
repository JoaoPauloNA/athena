"""Testes do recomendador (classificação, complexidade, economia)."""
import athena.recommend as rec


def test_classify_task_frontend():
    scores = rec.classify_task("refatorar o layout da tela em react com tailwind")
    assert max(scores, key=scores.get) == "frontend"


def test_classify_task_backend():
    scores = rec.classify_task("implementar endpoint novo na api com testes")
    assert max(scores, key=scores.get) == "backend"


def test_classify_task_rapidez():
    scores = rec.classify_task("renomear variável, coisa rápida e barata")
    assert max(scores, key=scores.get) == "rapidez"


def test_keyword_whole_word_redesign_is_not_design():
    """'redesign' NÃO pode casar com a keyword 'design' de frontend."""
    scores = rec.classify_task("redesign da arquitetura do sistema de pagamentos")
    assert "frontend" not in scores or scores.get("raciocinio", 0) >= scores["frontend"]


def test_estimate_complexity_simple():
    assert rec.estimate_complexity("corrigir typo e renomear função") == "simple"


def test_estimate_complexity_complex():
    assert rec.estimate_complexity("migração com race condition") == "complex"


def test_estimate_complexity_medium_default():
    assert rec.estimate_complexity("implementar endpoint de login") == "medium"


def _fake_catalog():
    return {
        "sonnet sonnet 5": [
            {"provider": "claude", "provider_name": "Claude", "model_id": "sonnet", "model_name": "Sonnet", "weight": "medium"},
        ],
        "opus opus 4.8": [
            {"provider": "claude", "provider_name": "Claude", "model_id": "opus", "model_name": "Opus", "weight": "heavy"},
        ],
        "haiku haiku 4.5": [
            {"provider": "claude", "provider_name": "Claude", "model_id": "haiku", "model_name": "Haiku", "weight": "light"},
        ],
    }


def _fake_ratings():
    return {
        "updated_at": "2026-08-07T00:00:00Z",
        "ttl_days": 7,
        "roles": rec.ROLE_LABELS,
        "models": [
            {"match": ["opus"], "name": "Opus", "maker": "A", "scores": {"backend": 9, "raciocinio": 9, "frontend": 8, "rapidez": 4}, "best_for": ["backend"], "note": "", "sources": []},
            {"match": ["sonnet"], "name": "Sonnet", "maker": "A", "scores": {"backend": 8, "raciocinio": 10, "frontend": 9, "rapidez": 6}, "best_for": ["raciocinio"], "note": "", "sources": []},
            {"match": ["haiku"], "name": "Haiku", "maker": "A", "scores": {"backend": 6, "raciocinio": 6, "frontend": 6, "rapidez": 10}, "best_for": ["rapidez"], "note": "", "sources": []},
        ],
    }


def test_recommend_excludes_heavy_for_simple(monkeypatch):
    monkeypatch.setattr(rec, "_providers_by_model", _fake_catalog)
    monkeypatch.setattr(rec, "load_ratings", _fake_ratings)
    r = rec.recommend_for_task("corrigir typo no readme, coisa simples")
    assert r["complexidade"] == "simple"
    nomes = [s["modelo"] for s in r["recomendacoes"]]
    assert "Opus" not in nomes  # heavy excluído
    assert "Haiku" in nomes


def test_recommend_allows_heavy_for_complex(monkeypatch):
    monkeypatch.setattr(rec, "_providers_by_model", _fake_catalog)
    monkeypatch.setattr(rec, "load_ratings", _fake_ratings)
    r = rec.recommend_for_task("redesign da arquitetura com concorrência")
    assert r["complexidade"] == "complex"
    nomes = [s["modelo"] for s in r["recomendacoes"]]
    assert "Opus" in nomes


def test_recommend_returns_tip(monkeypatch):
    monkeypatch.setattr(rec, "_providers_by_model", _fake_catalog)
    monkeypatch.setattr(rec, "load_ratings", _fake_ratings)
    r = rec.recommend_for_task("implementar endpoint na api")
    assert "provider" in r["dica"]
    assert r["funcao_detectada"]["role"] in rec.ROLE_LABELS
