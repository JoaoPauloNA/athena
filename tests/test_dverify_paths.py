"""Regressão: paths citados relativos à raiz do repo não são falso positivo."""
import subprocess

from athena import dverify


def test_repo_root_paths_accepted(tmp_path):
    """Agente trabalha em wd=subdir mas cita paths relativos à raiz do repo."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    sub = tmp_path / "projetos" / "site"
    sub.mkdir(parents=True)
    (tmp_path / "projetos" / "site" / "index.html").write_text("<html></html>")

    report = "Criei projetos/site/index.html com a landing completa."
    missing = dverify.find_missing_created_files(report, str(sub))
    assert missing == []


def test_truly_missing_still_flagged(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    report = "Criei projetos/site/fantasma.html e implementei tudo."
    missing = dverify.find_missing_created_files(report, str(tmp_path))
    assert "projetos/site/fantasma.html" in missing
