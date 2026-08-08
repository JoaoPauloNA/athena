"""Testes do verificador determinístico (claimed vs verified, sem modelo)."""
import subprocess

from athena import dverify
from athena.dverify import (
    deterministic_verify,
    extract_claimed_commands,
    find_missing_created_files,
)


def _fake_run_factory(exit_codes):
    """subprocess.run falso: devolve exit codes por comando."""
    calls = []

    def fake_run(argv, **kwargs):
        cmd = " ".join(argv)
        calls.append(cmd)
        code = exit_codes.get(cmd.split()[0], 0)
        return subprocess.CompletedProcess(argv, code, stdout="ok\n", stderr="")

    return fake_run, calls


def test_extract_pytest_claimed_success():
    cmds = extract_claimed_commands("Rodei pytest e todos os 34 testes passando ✅")
    assert cmds == ["pytest"]


def test_extract_ignores_admitted_failure():
    report = "Tentei rodar pytest mas pytest falhou com 2 erros; não consegui resolver."
    assert extract_claimed_commands(report) == []


def test_extract_dedupes_and_caps():
    report = "pytest passou. " * 10 + " ruff check ok. npm test ok. make test ok."
    cmds = extract_claimed_commands(report)
    assert len(cmds) <= dverify.MAX_COMMANDS
    assert cmds.count("pytest") == 1


def test_allowed_whitelist():
    assert dverify._is_allowed("pytest")
    assert dverify._is_allowed("python -m pytest tests/")
    assert dverify._is_allowed("ruff check athena/")
    assert not dverify._is_allowed("rm -rf /")
    assert not dverify._is_allowed("python script.py")
    assert not dverify._is_allowed("go build ./...")


def test_deterministic_false_on_failed_exit(tmp_path, monkeypatch):
    fake_run, _ = _fake_run_factory({"pytest": 1})
    monkeypatch.setattr(dverify.subprocess, "run", fake_run)
    v = deterministic_verify("Rodei pytest: 34 passed, tudo verde ✅", str(tmp_path))
    assert v.verdadeiro is False
    assert any("exit 1" in m for m in v.motivos)


def test_deterministic_true_on_passing_exit(tmp_path, monkeypatch):
    fake_run, _ = _fake_run_factory({"pytest": 0})
    monkeypatch.setattr(dverify.subprocess, "run", fake_run)
    v = deterministic_verify("Rodei pytest e os testes passando ✅", str(tmp_path))
    assert v.verdadeiro is True
    assert any("exit 0" in m for m in v.motivos)


def test_deterministic_false_on_missing_created_file(tmp_path, monkeypatch):
    fake_run, _ = _fake_run_factory({})
    monkeypatch.setattr(dverify.subprocess, "run", fake_run)
    v = deterministic_verify("Criei o arquivo novo_modulo.py com a feature.", str(tmp_path))
    assert v.verdadeiro is False
    assert "novo_modulo.py" in v.missing_files


def test_deterministic_none_when_nothing_verifiable(tmp_path):
    v = deterministic_verify("Atualizei a documentação do projeto.", str(tmp_path))
    assert v.verdadeiro is None
    assert v.checks == []


def test_advisory_mode_disables_dverify(tmp_path, monkeypatch):
    monkeypatch.setenv("ATHENA_VERIFY_MODE", "advisory")
    v = deterministic_verify("Rodei pytest, tudo passando", str(tmp_path))
    assert v.verdadeiro is None


def test_find_missing_created_files_ignores_existing(tmp_path):
    (tmp_path / "real.py").write_text("x = 1")
    missing = find_missing_created_files("Criei real.py e implementei a função.", str(tmp_path))
    assert missing == []


def test_verify_report_prefers_deterministic(tmp_path, monkeypatch):
    """verify_report deve devolver veredito determinístico sem chamar modelo."""
    from athena import verifier

    fake_run, _ = _fake_run_factory({"pytest": 2})
    monkeypatch.setattr(dverify.subprocess, "run", fake_run)
    monkeypatch.setattr(
        verifier, "pick_verifier",
        lambda _p: (_ for _ in ()).throw(AssertionError("não deveria chamar modelo")),
    )
    v = verifier.verify_report(
        "tarefa", "Rodei pytest: 34 passed ✅", working_directory=str(tmp_path)
    )
    assert v.verdadeiro is False
    assert v.verificador == "deterministic"
    assert v.confianca == "alta"
