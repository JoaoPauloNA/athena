"""Testes do verificador determinístico (claimed vs verified, sem modelo)."""
from types import SimpleNamespace

from athena import dverify
from athena.dverify import (
    CommandResult,
    deterministic_verify,
    extract_claimed_commands,
    find_missing_created_files,
)
from athena.execution import DeadlineBudget


def _fake_run_factory(exit_codes):
    """bridge.run_subprocess falso: devolve exit codes por comando."""
    calls = []

    def fake_run(_provider, argv, **kwargs):
        cmd = " ".join(list(argv))
        calls.append(cmd)
        code = exit_codes.get(cmd.split()[0], 0)
        return SimpleNamespace(
            exit_code=code,
            stdout="ok\n",
            stderr="",
            timed_out=False,
            error=None,
            execution={"state": "COMPLETED"},
        )

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
    monkeypatch.setattr(dverify, "run_subprocess", fake_run)
    v = deterministic_verify("Rodei pytest: 34 passed, tudo verde ✅", str(tmp_path))
    assert v.verdadeiro is False
    assert any("exit 1" in m for m in v.motivos)


def test_deterministic_true_on_passing_exit(tmp_path, monkeypatch):
    fake_run, _ = _fake_run_factory({"pytest": 0})
    monkeypatch.setattr(dverify, "run_subprocess", fake_run)
    v = deterministic_verify("Rodei pytest e os testes passando ✅", str(tmp_path))
    assert v.verdadeiro is True
    assert any("exit 0" in m for m in v.motivos)


def test_deterministic_false_on_missing_created_file(tmp_path, monkeypatch):
    fake_run, _ = _fake_run_factory({})
    monkeypatch.setattr(dverify, "run_subprocess", fake_run)
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
    missing, _, timed_out = find_missing_created_files("Criei real.py e implementei a função.", str(tmp_path))
    assert missing == []
    assert timed_out is False


def test_find_missing_created_files_propagates_cancelled_repo_probe(tmp_path, monkeypatch):
    def fake_run(_provider, _argv, **_kwargs):
        return SimpleNamespace(
            exit_code=130,
            stdout="",
            stderr="",
            timed_out=False,
            error="user_requested",
            execution={"state": "CANCELLED"},
        )

    monkeypatch.setattr(dverify, "run_subprocess", fake_run)
    missing, execution, timed_out = find_missing_created_files("Criei modulo.py", str(tmp_path))
    assert missing == ["modulo.py"]
    assert execution == {"state": "CANCELLED"}
    assert timed_out is False


def test_deterministic_verify_propagates_termination_unconfirmed_without_claimed_commands(
    tmp_path, monkeypatch
):
    def fake_run(_provider, _argv, **_kwargs):
        return SimpleNamespace(
            exit_code=125,
            stdout="",
            stderr="",
            timed_out=False,
            error="indeterminate",
            execution={"state": "TERMINATION_UNCONFIRMED"},
        )

    monkeypatch.setattr(dverify, "run_subprocess", fake_run)
    verdict = deterministic_verify("Atualizei a documentação", str(tmp_path))
    assert verdict.verdadeiro is None
    assert verdict.termination_unconfirmed is True
    assert verdict.execution == {"state": "TERMINATION_UNCONFIRMED"}


def test_deterministic_verify_propagates_cancelled_without_claimed_commands(tmp_path, monkeypatch):
    def fake_run(_provider, _argv, **_kwargs):
        return SimpleNamespace(
            exit_code=130,
            stdout="",
            stderr="",
            timed_out=False,
            error="user_requested",
            execution={"state": "CANCELLED"},
        )

    monkeypatch.setattr(dverify, "run_subprocess", fake_run)
    verdict = deterministic_verify("Atualizei a documentação", str(tmp_path))
    assert verdict.verdadeiro is None
    assert verdict.execution == {"state": "CANCELLED"}


def test_deterministic_verify_marks_deadline_exhausted_on_cancelled_timed_out_repo_probe(
    tmp_path, monkeypatch
):
    def fake_run(_provider, _argv, **_kwargs):
        return SimpleNamespace(
            exit_code=124,
            stdout="",
            stderr="",
            timed_out=True,
            error="verification_deadline",
            execution={"state": "CANCELLED"},
        )

    monkeypatch.setattr(dverify, "run_subprocess", fake_run)
    verdict = deterministic_verify("Atualizei a documentação", str(tmp_path))
    assert verdict.verdadeiro is None
    assert verdict.deadline_exhausted is True
    assert verdict.timed_out is True


def test_command_result_to_dict_omits_execution_by_default_and_truncates_output():
    result = CommandResult(
        command="pytest",
        exit_code=1,
        ok=False,
        output_tail="x" * 600,
        error="boom",
        execution={"state": "FAILED"},
    )
    payload = result.to_dict()
    assert "execution" not in payload
    assert payload["output_tail"] == "x" * 500


def test_verify_report_prefers_deterministic(tmp_path, monkeypatch):
    """verify_report deve devolver veredito determinístico sem chamar modelo."""
    from athena import verifier

    fake_run, _ = _fake_run_factory({"pytest": 2})
    monkeypatch.setattr(dverify, "run_subprocess", fake_run)
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
    assert '"output_tail"' not in v.evidencias
    assert '"execution"' not in v.evidencias


def test_deterministic_verify_does_not_start_checks_after_budget_expires(tmp_path, monkeypatch):
    monkeypatch.setattr(dverify, "extract_claimed_commands", lambda _r: ["pytest", "ruff check ."])
    calls = {"count": 0}

    def fake_run(_provider, _argv, **_kwargs):
        calls["count"] += 1
        return SimpleNamespace(
            exit_code=0,
            stdout="ok",
            stderr="",
            timed_out=False,
            error=None,
            execution={"state": "COMPLETED"},
        )

    monkeypatch.setattr(dverify, "run_subprocess", fake_run)
    monotonic = {"now": 100.0}
    monkeypatch.setattr("athena.execution.time.monotonic", lambda: monotonic["now"])
    budget = DeadlineBudget(1.0)
    # Expire deterministically without depending on the host clock resolution.
    monotonic["now"] = 101.1
    verdict = dverify.deterministic_verify(
        "Rodei pytest e ruff check .",
        str(tmp_path),
        budget=budget,
    )
    assert verdict.deadline_exhausted is True
    assert calls["count"] == 0


def test_run_command_uses_remaining_budget_as_timeout(tmp_path, monkeypatch):
    observed = {}

    def fake_run(_provider, _argv, **kwargs):
        observed["timeout"] = kwargs["timeout"]
        return SimpleNamespace(
            exit_code=0,
            stdout="ok",
            stderr="",
            timed_out=False,
            error=None,
            execution={"state": "COMPLETED"},
        )

    monkeypatch.setattr(dverify, "run_subprocess", fake_run)
    budget = DeadlineBudget(1.0)
    result = dverify.run_command("pytest", str(tmp_path), budget=budget)
    assert result.ok is True
    assert observed["timeout"] <= dverify.COMMAND_TIMEOUT
    assert observed["timeout"] <= 1.0
