"""Testes da persistência de vereditos e do ranking de confiabilidade."""
import json

from athena import reliability
from athena.reliability import list_verdicts, record_verdict, reliability_report
from athena.verifier import Verdict


def _use_tmp_file(monkeypatch, tmp_path):
    target = tmp_path / "verdicts.json"
    monkeypatch.setattr(reliability, "VERDICTS_FILE", target)
    return target


def test_record_and_report(monkeypatch, tmp_path):
    target = _use_tmp_file(monkeypatch, tmp_path)
    record_verdict("claude", Verdict(verdadeiro=True, verificador="deterministic"), task_excerpt="t1")
    record_verdict("claude", Verdict(verdadeiro=True, verificador="deterministic"), task_excerpt="t2")
    record_verdict("claude", Verdict(verdadeiro=False, motivos=["exit 1"], verificador="deterministic"))
    record_verdict("codex", Verdict(verdadeiro=False, escalado=True, verificador="opencode/x"))

    data = json.loads(target.read_text())
    assert len(data) == 4
    assert data[0]["executor"] == "claude"
    assert data[0]["camada"] == "deterministic"
    assert data[3]["camada"] == "advisory"

    report = reliability_report()
    assert report["claude"]["verdadeiros"] == 2
    assert report["claude"]["falsos"] == 1
    assert report["claude"]["taxa_falso"] == round(1 / 3, 3)
    assert report["claude"]["confiabilidade"] == round(2 / 3, 3)
    assert report["codex"]["escalados"] == 1
    assert report["codex"]["taxa_falso"] == 1.0


def test_report_counts_unavailable_episodes(monkeypatch, tmp_path):
    _use_tmp_file(monkeypatch, tmp_path)
    record_verdict("agy", Verdict(verdadeiro=None, motivos=["sem verificador"]))
    report = reliability_report()
    assert report["agy"]["indisponiveis"] == 1
    assert report["agy"]["taxa_falso"] is None  # não entra na taxa


def test_ranking_orders_most_reliable_first(monkeypatch, tmp_path):
    _use_tmp_file(monkeypatch, tmp_path)
    record_verdict("bom", Verdict(verdadeiro=True))
    record_verdict("ruim", Verdict(verdadeiro=False))
    ranking = list(reliability_report().keys())
    assert ranking == ["bom", "ruim"]


def test_motivos_are_redacted_and_truncated(monkeypatch, tmp_path):
    target = _use_tmp_file(monkeypatch, tmp_path)
    motivos = ["x" * 500, "a", "b", "c", "d"]  # >3 motivos, um gigante
    record_verdict("claude", Verdict(verdadeiro=False, motivos=motivos))
    data = json.loads(target.read_text())
    assert len(data[0]["motivos"]) == 3
    assert len(data[0]["motivos"][0]) <= 120


def test_task_excerpt_is_capped(monkeypatch, tmp_path):
    target = _use_tmp_file(monkeypatch, tmp_path)
    record_verdict("claude", Verdict(verdadeiro=True), task_excerpt="y" * 500)
    assert len(json.loads(target.read_text())[0]["tarefa"]) <= 80


def test_retention_caps_records(monkeypatch, tmp_path):
    target = _use_tmp_file(monkeypatch, tmp_path)
    monkeypatch.setattr(reliability, "_MAX_RECORDS", 10)
    for i in range(15):
        record_verdict("claude", Verdict(verdadeiro=True), task_excerpt=f"t{i}")
    data = json.loads(target.read_text())
    assert len(data) == 10
    assert data[-1]["tarefa"] == "t14"


def test_list_verdicts_returns_latest(monkeypatch, tmp_path):
    _use_tmp_file(monkeypatch, tmp_path)
    for i in range(5):
        record_verdict("claude", Verdict(verdadeiro=True), task_excerpt=f"t{i}")
    latest = list_verdicts(limit=2)
    assert [v["tarefa"] for v in latest] == ["t3", "t4"]


def test_empty_report(monkeypatch, tmp_path):
    _use_tmp_file(monkeypatch, tmp_path)
    assert reliability_report() == {}
    assert list_verdicts() == []
