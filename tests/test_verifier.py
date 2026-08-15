"""Testes do verificador (detector de mentiras)."""
import subprocess
from types import SimpleNamespace

import pytest

from athena.execution import ExecutionState
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
    ev, _, timed_out = collect_evidence(str(tmp_path), "Alterei existe.py e criei fantasma.py")
    assert "EXISTE existe.py" in ev
    assert "NÃO EXISTE fantasma.py" in ev
    assert timed_out is False


def test_collect_evidence_git_subdir(tmp_path):
    """git rev-parse sobe a árvore: subdiretório de um repo deve achar o repo."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    sub = tmp_path / "pkg" / "mod"
    sub.mkdir(parents=True)
    ev, _, timed_out = collect_evidence(str(sub), "relatório qualquer")
    assert "repo git:" in ev
    assert timed_out is False


def test_collect_evidence_stops_on_first_cancelled(monkeypatch, tmp_path):
    calls = {"count": 0}

    def fake_run(_provider, _argv, **_kwargs):
        calls["count"] += 1
        return SimpleNamespace(
            exit_code=130,
            stdout="",
            stderr="",
            timed_out=False,
            error="user_requested",
            execution={"state": "CANCELLED", "source": calls["count"]},
        )

    monkeypatch.setattr("athena.verifier.run_subprocess", fake_run)
    _, execution, timed_out = collect_evidence(str(tmp_path), "nada")
    assert calls["count"] == 1
    assert execution == {"state": "CANCELLED", "source": 1}
    assert timed_out is False


def test_collect_evidence_stops_on_first_termination_unconfirmed(monkeypatch, tmp_path):
    calls = {"count": 0}

    def fake_run(_provider, argv, **_kwargs):
        calls["count"] += 1
        if argv[0:2] == ["git", "status"]:
            return SimpleNamespace(
                exit_code=125,
                stdout="",
                stderr="",
                timed_out=False,
                error="indeterminate",
                execution={"state": "TERMINATION_UNCONFIRMED", "source": calls["count"]},
            )
        return SimpleNamespace(
            exit_code=0,
            stdout=str(tmp_path),
            stderr="",
            timed_out=False,
            error=None,
            execution={"state": "COMPLETED", "source": calls["count"]},
        )

    monkeypatch.setattr("athena.verifier.run_subprocess", fake_run)
    _, execution, timed_out = collect_evidence(str(tmp_path), "nada")
    assert calls["count"] == 2
    assert execution == {"state": "TERMINATION_UNCONFIRMED", "source": 2}
    assert timed_out is False


def test_verify_report_cancelled_uses_ordered_phase_transitions(monkeypatch, tmp_path):
    from athena import verifier

    monkeypatch.setattr(
        "athena.dverify.deterministic_verify",
        lambda *args, **kwargs: SimpleNamespace(
            checks=[SimpleNamespace(execution={"state": "CANCELLED"}, timed_out=False)],
            termination_unconfirmed=False,
            deadline_exhausted=False,
            timed_out=False,
            verdadeiro=None,
            motivos=[],
            to_dict=lambda **_k: {},
        ),
        raising=False,
    )
    verdict = verifier.verify_report("task", "report", working_directory=str(tmp_path))
    assert verdict.execution is not None
    history = [item["to_state"] for item in verdict.execution["history"]]
    assert history[-3:] == [
        ExecutionState.CANCELLATION_REQUESTED.value,
        ExecutionState.TERMINATING.value,
        ExecutionState.CANCELLED.value,
    ]


def test_verify_report_propagates_cancelled_repo_probe_without_checks(monkeypatch, tmp_path):
    from athena import verifier

    monkeypatch.setattr(
        "athena.dverify.deterministic_verify",
        lambda *args, **kwargs: SimpleNamespace(
            checks=[],
            execution={"state": "CANCELLED"},
            termination_unconfirmed=False,
            deadline_exhausted=False,
            timed_out=False,
            verdadeiro=None,
            motivos=[],
            to_dict=lambda **_k: {},
        ),
    )
    verdict = verifier.verify_report("task", "report", working_directory=str(tmp_path))
    assert verdict.verdadeiro is None
    assert verdict.execution is not None
    assert verdict.execution["state"] == ExecutionState.CANCELLED.value


def test_verify_report_termination_unconfirmed_uses_terminating_before_terminal(monkeypatch, tmp_path):
    from athena import verifier

    monkeypatch.setattr(
        "athena.dverify.deterministic_verify",
        lambda *args, **kwargs: SimpleNamespace(
            checks=[],
            termination_unconfirmed=True,
            deadline_exhausted=False,
            timed_out=False,
            verdadeiro=None,
            motivos=[],
            to_dict=lambda **_k: {},
        ),
        raising=False,
    )
    verdict = verifier.verify_report("task", "report", working_directory=str(tmp_path))
    assert verdict.execution is not None
    history = [item["to_state"] for item in verdict.execution["history"]]
    assert history[-2:] == [
        ExecutionState.TERMINATING.value,
        ExecutionState.TERMINATION_UNCONFIRMED.value,
    ]


def test_verify_report_marks_timed_out_when_budget_expired_before_stages(tmp_path, monkeypatch):
    from athena import verifier

    monkeypatch.setattr(
        "athena.dverify.deterministic_verify",
        lambda *args, **kwargs: SimpleNamespace(
            checks=[],
            termination_unconfirmed=False,
            deadline_exhausted=True,
            timed_out=True,
            verdadeiro=None,
            motivos=[],
            to_dict=lambda **_k: {},
        ),
        raising=False,
    )
    verdict = verifier.verify_report(
        "task",
        "report",
        working_directory=str(tmp_path),
        verification_timeout_s=600,
    )
    assert verdict.execution is not None
    assert verdict.execution["state"] == ExecutionState.TIMED_OUT.value
    assert verdict.execution["process_created"] is False
    assert verdict.execution["termination_reason"] == "verification_deadline"


def test_verify_report_advisory_uses_timeout_capped_by_remaining(monkeypatch, tmp_path):
    from athena import verifier

    monkeypatch.setattr(
        "athena.dverify.deterministic_verify",
        lambda *args, **kwargs: SimpleNamespace(
            checks=[],
            termination_unconfirmed=False,
            deadline_exhausted=False,
            timed_out=False,
            verdadeiro=None,
            motivos=[],
            to_dict=lambda **_k: {},
        ),
        raising=False,
    )
    monkeypatch.setattr(verifier, "pick_verifier", lambda _p: ("p", None))
    monkeypatch.setattr(
        verifier,
        "collect_evidence",
        lambda *args, **kwargs: ("evidence", {"state": "COMPLETED"}, False),
    )
    observed = {}

    def fake_ask_provider(*args, **kwargs):
        observed["timeout"] = kwargs["timeout"]
        observed["execution_id"] = kwargs.get("execution_id")
        observed["attempt_id"] = kwargs.get("attempt_id")
        assert kwargs.get("on_execution_update") is None
        return SimpleNamespace(
            output='{"verdadeiro": true, "confianca": "alta", "motivos": []}',
            execution={"state": "COMPLETED"},
            timed_out=False,
        )

    monkeypatch.setattr("athena.providers.ask_provider", fake_ask_provider)
    verdict = verifier.verify_report(
        "task",
        "report",
        working_directory=str(tmp_path),
        verification_timeout_s=1.5,
    )
    assert verdict.verdadeiro is True
    assert observed["timeout"] <= 1.5
    assert observed["execution_id"] is not None
    assert observed["attempt_id"] is not None


def test_verify_report_phase_configures_absolute_deadline(monkeypatch, tmp_path):
    from athena import verifier

    monkeypatch.setattr(
        "athena.dverify.deterministic_verify",
        lambda *args, **kwargs: SimpleNamespace(
            checks=[],
            termination_unconfirmed=False,
            deadline_exhausted=False,
            timed_out=False,
            verdadeiro=True,
            motivos=["ok"],
            to_dict=lambda **_k: {},
        ),
        raising=False,
    )
    verdict = verifier.verify_report(
        "task",
        "report",
        working_directory=str(tmp_path),
        verification_timeout_s=7.5,
    )
    assert verdict.execution is not None
    assert verdict.execution["absolute_deadline_s"] == 7.5


def test_verify_report_advisory_timeout_precedes_explicit_cancelled(monkeypatch, tmp_path):
    from athena import verifier

    monkeypatch.setattr(
        "athena.dverify.deterministic_verify",
        lambda *args, **kwargs: SimpleNamespace(
            checks=[],
            termination_unconfirmed=False,
            deadline_exhausted=False,
            timed_out=False,
            verdadeiro=None,
            motivos=[],
            to_dict=lambda **_k: {},
        ),
        raising=False,
    )
    monkeypatch.setattr(verifier, "pick_verifier", lambda _p: ("p", None))
    monkeypatch.setattr(
        verifier,
        "collect_evidence",
        lambda *args, **kwargs: ("evidence", {"state": "COMPLETED"}, False),
    )

    def fake_ask_provider(*args, **kwargs):
        return SimpleNamespace(
            output="",
            execution={"state": "CANCELLED"},
            timed_out=True,
        )

    monkeypatch.setattr("athena.providers.ask_provider", fake_ask_provider)
    verdict = verifier.verify_report("task", "report", working_directory=str(tmp_path))
    assert verdict.execution is not None
    assert verdict.execution["state"] == ExecutionState.TIMED_OUT.value
    assert verdict.execution["termination_reason"] == "verification_deadline"


def test_verify_report_rejects_non_finite_timeout(tmp_path):
    from athena import verifier

    with pytest.raises(ValueError):
        verifier.verify_report(
            "task",
            "report",
            working_directory=str(tmp_path),
            verification_timeout_s=float("inf"),
        )
