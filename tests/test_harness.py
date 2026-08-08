"""Testes do mini-harness (sem rodar CLIs de verdade)."""
import shutil
from pathlib import Path

from harness.run import _claimed_from_report, load_suite, prepare_workspace, run_oracle


def test_manifest_valid():
    suite = load_suite()
    assert len(suite["tasks"]) == 6
    niveis = {t["nivel"] for t in suite["tasks"]}
    assert niveis == {"easy", "medium", "hard"}
    categorias = {t["categoria"] for t in suite["tasks"]}
    assert "honestidade" in categorias
    assert "multi-arquivo-regressao" in categorias


def test_manifest_tasks_have_oracle_and_files():
    suite = load_suite()
    for task in suite["tasks"]:
        assert task["oracle"], task["id"]
        assert task["arquivos"], task["id"]
        assert any(f.startswith("test_") for f in task["arquivos"]), task["id"]


def test_prepare_workspace_materializes_files():
    suite = load_suite()
    task = suite["tasks"][0]
    workdir = prepare_workspace(task)
    try:
        content = (Path(workdir) / "test_soma.py").read_text()
        assert "soma(2, 3) == 5" in content
        assert not (Path(workdir) / "soma.py").exists()  # agente é quem cria
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def test_run_oracle_pass_and_fail(tmp_path):
    import sys
    oracle = [f'"{sys.executable}" -m pytest']
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    ok = run_oracle(oracle, str(tmp_path))
    assert ok["verified"] is True
    assert ok["checks"][0]["exit_code"] == 0

    (tmp_path / "test_bad.py").write_text("def test_bad():\n    assert False\n")
    bad = run_oracle(oracle, str(tmp_path))
    assert bad["verified"] is False


def test_claimed_detection():
    assert _claimed_from_report("Criei soma.py e o pytest passou ✅") is True
    assert _claimed_from_report("Não consegui: os testes são contraditórios, pytest falhou") is False
