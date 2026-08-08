"""Testes do verificador (detector de mentiras)."""
import subprocess

from athena.verifier import (
    Verdict,
    _parse_verdict,
    build_fix_prompt,
    collect_evidence,
)


def test_parse_verdict_clean_json():
    assert _parse_verdict('{"verdadeiro": true, "confianca": "alta", "motivos": []}')


def test_parse_verdict_with_surrounding_text():
    out = 'Aqui está: {"verdadeiro": false, "confianca": "media", "motivos": ["x"]} fim.'
    parsed = _parse_verdict(out)
    assert parsed and parsed["verdadeiro"] is False


def test_parse_verdict_rejects_garbage():
    assert _parse_verdict("sem json aqui") is None
    assert _parse_verdict('{"outro": true}') is None
    assert _parse_verdict("") is None


def test_build_fix_prompt_includes_reasons():
    v = Verdict(verdadeiro=False, motivos=["arquivo não existe"], evidencias="git limpo")
    prompt = build_fix_prompt("tarefa original", v)
    assert "arquivo não existe" in prompt
    assert "git limpo" in prompt
    assert "tarefa original" in prompt
    assert "FALSO" in prompt


def test_collect_evidence_lists_claimed_files(tmp_path):
    (tmp_path / "existe.py").write_text("x = 1")
    ev = collect_evidence(str(tmp_path), "Alterei existe.py e criei fantasma.py")
    assert "EXISTE existe.py" in ev
    assert "NÃO EXISTE fantasma.py" in ev


def test_collect_evidence_git_subdir(tmp_path):
    """git rev-parse sobe a árvore: subdiretório de um repo deve achar o repo."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    sub = tmp_path / "pkg" / "mod"
    sub.mkdir(parents=True)
    ev = collect_evidence(str(sub), "relatório qualquer")
    assert "repo git:" in ev
