"""Testes da tabela de ratings."""
from athena import ratings


def test_match_score_longest_needle_wins():
    entry = {"match": ["gpt", "gpt-5.6"]}
    assert ratings._match_score(entry, "gpt-5.6-sol-high", "") > ratings._match_score(
        {"match": ["gpt"]}, "gpt-5.6-sol-high", ""
    )


def test_match_score_no_match():
    assert ratings._match_score({"match": ["opus"]}, "gpt-5.5", "GPT 5.5") == 0


def test_load_ratings_seeds_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(ratings, "RATINGS_FILE", tmp_path / "ratings.json")
    data = ratings.load_ratings()
    assert data["models"]
    assert (tmp_path / "ratings.json").exists()
    assert data["roles"] == ratings.ROLE_LABELS


def test_best_per_role_sorted(tmp_path, monkeypatch):
    monkeypatch.setattr(ratings, "RATINGS_FILE", tmp_path / "ratings.json")
    roles = ratings.best_per_role()
    for entries in roles.values():
        scores = [e["score"] for e in entries]
        assert scores == sorted(scores, reverse=True)


def test_best_per_role_marks_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(ratings, "RATINGS_FILE", tmp_path / "ratings.json")
    roles = ratings.best_per_role(["sonnet claude sonnet 5"])
    sonnet = next(e for e in roles["raciocinio"] if "Sonnet" in e["name"])
    assert sonnet["installed"] is True
