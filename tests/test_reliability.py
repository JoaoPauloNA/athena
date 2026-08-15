"""Testes da persistência de vereditos e do ranking de confiabilidade."""
import json

import pytest

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
    assert "tarefa" not in data[0]
    assert "projeto" not in data[0]
    assert "motivos" not in data[0]
    assert "reason_codes" in data[0]

    report = reliability_report()
    assert report["claude"]["verdadeiros"] == 2
    assert report["claude"]["falsos"] == 1
    assert report["claude"]["taxa_falso"] == round(1 / 3, 3)
    assert report["claude"]["confiabilidade"] == round(2 / 3, 3)
    assert report["codex"]["escalados"] == 1
    assert report["codex"]["taxa_falso"] == 1.0
    assert data[3]["verificador"] == "opencode/x"


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


def test_reason_codes_are_redacted_and_capped(monkeypatch, tmp_path):
    target = _use_tmp_file(monkeypatch, tmp_path)
    motivos = ["x" * 500, "a", "b", "c", "d"]  # >3 motivos, um gigante
    record_verdict("claude", Verdict(verdadeiro=False, motivos=motivos))
    data = json.loads(target.read_text())
    assert len(data[0]["reason_codes"]) == 3
    assert data[0]["reason_codes"][0] == "other_redacted"


def test_task_excerpt_and_project_are_ignored(monkeypatch, tmp_path):
    target = _use_tmp_file(monkeypatch, tmp_path)
    record_verdict("claude", Verdict(verdadeiro=True), task_excerpt="y" * 500, project="/tmp/secret-project")
    payload = json.loads(target.read_text())[0]
    assert "tarefa" not in payload
    assert "projeto" not in payload


def test_record_verdict_is_best_effort_when_storage_fails(monkeypatch):
    def fail_save(_records):
        raise PermissionError("read-only")

    monkeypatch.setattr(reliability, "_save", fail_save)

    # Telemetry has no authority over a completed execution result.
    assert record_verdict("claude", Verdict(verdadeiro=True)) is None


def test_retention_caps_records(monkeypatch, tmp_path):
    target = _use_tmp_file(monkeypatch, tmp_path)
    monkeypatch.setattr(reliability, "_MAX_RECORDS", 10)
    for i in range(15):
        record_verdict("claude", Verdict(verdadeiro=True), task_excerpt=f"t{i}")
    data = json.loads(target.read_text())
    assert len(data) == 10
    assert data[-1]["executor"] == "claude"


def test_list_verdicts_returns_latest(monkeypatch, tmp_path):
    _use_tmp_file(monkeypatch, tmp_path)
    for i in range(5):
        record_verdict("claude", Verdict(verdadeiro=True), task_excerpt=f"t{i}")
    latest = list_verdicts(limit=2)
    assert len(latest) == 2
    assert all("tarefa" not in item and "projeto" not in item for item in latest)


def test_load_migrates_legacy_records_without_leaks(monkeypatch, tmp_path):
    target = _use_tmp_file(monkeypatch, tmp_path)
    target.write_text(
        json.dumps(
            [
                {
                    "ts": "2026-01-01T00:00:00",
                    "executor": "codex",
                    "camada": "deterministic",
                    "verificador": "deterministic",
                    "verdadeiro": True,
                    "confianca": "alta",
                    "motivos": ["/tmp/path leaked", "user prompt leaked"],
                    "tarefa": "sensitive prompt",
                    "projeto": "/very/secret/path",
                }
            ]
        )
    )
    records = list_verdicts(limit=5)
    assert len(records) == 1
    serialized = json.dumps(records[0], ensure_ascii=False)
    assert "sensitive prompt" not in serialized
    assert "/very/secret/path" not in serialized
    assert "/tmp/path" not in serialized
    assert "reason_codes" in records[0]


def test_load_ignores_corrupted_legacy_entries_without_raising(monkeypatch, tmp_path):
    target = _use_tmp_file(monkeypatch, tmp_path)
    target.write_text(json.dumps([{"executor": "ok"}, "bad", 123, {"tentativas": "oops"}]))
    records = reliability._load()
    assert len(records) == 2
    assert all(isinstance(item, dict) for item in records)
    assert records[1]["executor"] == "redacted_identifier"


def test_strict_types_for_verdadeiro_and_tentativas(monkeypatch, tmp_path):
    target = _use_tmp_file(monkeypatch, tmp_path)
    target.write_text(
        json.dumps(
            [
                {"executor": "x", "verdadeiro": 1, "tentativas": "not-int", "escalado": "yes"},
                {"executor": "y", "verdadeiro": False, "tentativas": 5, "escalado": True},
            ]
        )
    )
    records = reliability._load()
    assert records[0]["verdadeiro"] is None
    assert records[0]["tentativas"] == 1
    assert records[0]["escalado"] is False
    assert records[1]["verdadeiro"] is False
    assert records[1]["tentativas"] == 5
    assert records[1]["escalado"] is True
    serialized = json.dumps(records, ensure_ascii=False)
    assert "not-int" not in serialized
    assert "yes" not in serialized


def test_save_is_atomic_uses_tempfile_and_replace(monkeypatch, tmp_path):
    _use_tmp_file(monkeypatch, tmp_path)
    replaced: dict[str, str] = {}

    real_replace = reliability.os.replace

    def tracked_replace(src, dst):
        replaced["src"] = str(src)
        replaced["dst"] = str(dst)
        return real_replace(src, dst)

    monkeypatch.setattr(reliability.os, "replace", tracked_replace)
    reliability._save([{"executor": "x"}])
    assert replaced["src"] != replaced["dst"]
    assert replaced["dst"].endswith("verdicts.json")


def test_list_limit_is_safely_bounded(monkeypatch, tmp_path):
    _use_tmp_file(monkeypatch, tmp_path)
    for i in range(3):
        record_verdict(f"p{i}", Verdict(verdadeiro=True))
    assert list_verdicts(limit=True) == []
    assert len(list_verdicts(limit="2")) == 2
    assert len(list_verdicts(limit=999999)) == 3


def test_empty_report(monkeypatch, tmp_path):
    _use_tmp_file(monkeypatch, tmp_path)
    assert reliability_report() == {}
    assert list_verdicts() == []


def test_sanitize_ts_rejects_arbitrary_text_and_uses_now():
    ts = reliability._sanitize_ts("SUPER_SECRET_PATH")
    assert "SUPER_SECRET_PATH" not in ts
    assert "T" in ts
    assert ts.endswith("Z")


def test_redacts_suspicious_executor_and_verifier_identifiers(monkeypatch, tmp_path):
    target = _use_tmp_file(monkeypatch, tmp_path)
    record_verdict(
        "sk-abc123TOKEN=/Users/private",
        Verdict(verdadeiro=True, verificador="TOKEN=/Users/private"),
    )
    payload = json.loads(target.read_text())[0]
    assert payload["executor"] == "redacted_identifier"
    assert payload["verificador"] == "redacted_identifier"


def test_save_cleans_tempfile_when_replace_fails(monkeypatch, tmp_path):
    _use_tmp_file(monkeypatch, tmp_path)
    tmp_files_before = {p.name for p in tmp_path.iterdir()}

    def fail_replace(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(reliability.os, "replace", fail_replace)
    with pytest.raises(OSError):
        reliability._save([{"executor": "codex"}])
    tmp_files_after = {p.name for p in tmp_path.iterdir()}
    assert tmp_files_after == tmp_files_before
