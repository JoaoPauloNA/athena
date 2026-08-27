"""Contrato do sink local, determinístico e atômico do EG-3A."""

from __future__ import annotations

import json
import stat

import pytest

from athena.evidence_gate.sink import AtomicJsonFileSink


def _report():
    return {
        "pipeline": "eg3a",
        "ran": True,
        "execution_status": "completed",
        "validation_status": "inconclusive",
        "delivery_status": "awaiting_human_review",
        "reason_codes": ["MISSING_EVIDENCE"],
    }


def test_sink_escreve_json_estavel_com_metadados_sanitizados(tmp_path):
    sink = AtomicJsonFileSink(tmp_path)
    first = sink.write(_report(), execution_id="exec/../../segredo", tool="run_combo")
    second = sink.write(_report(), execution_id="exec/../../segredo", tool="run_combo")

    assert first == second
    assert first.parent == tmp_path
    assert "/" not in first.name and ".." not in first.name
    stored = json.loads(first.read_text())
    assert stored == {
        "schema_version": "athena.eg3a.sink.v1",
        "metadata": {"execution_id": "exec-segredo", "tool": "run_combo"},
        "report": _report(),
    }
    assert stat.S_IMODE(first.stat().st_mode) == 0o600
    assert [path for path in tmp_path.iterdir() if path.name.startswith(".eg3a-")] == []


def test_sink_falha_sem_deixar_arquivo_parcial(tmp_path, monkeypatch):
    sink = AtomicJsonFileSink(tmp_path)

    def broken_replace(source, destination):
        raise OSError("prompt-secreto")

    monkeypatch.setattr("athena.evidence_gate.sink.os.replace", broken_replace)
    with pytest.raises(OSError):
        sink(_report(), execution_id="exec-1", tool="run_combo")

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("directory", ["relative/path", ""])
def test_sink_exige_diretorio_absoluto_explicito(directory):
    with pytest.raises(ValueError):
        AtomicJsonFileSink(directory)
