"""Testes do pipeline modular de verificação."""

from __future__ import annotations

import ast
import sys
import time
from pathlib import Path

from athena.execution import CancellationToken, ExecutionDeadlines, ExecutionState
from athena.verifier import (
    CommandClaim,
    FileClaim,
    FindingStatus,
    VerificationFinding,
    VerificationPhase,
    VerificationRequest,
    resolve_claimed_file,
    verify,
)


def test_file_claim_is_resolved_from_working_directory(tmp_path: Path) -> None:
    claimed = tmp_path / "artifact.txt"
    claimed.write_text("evidence", encoding="utf-8")

    result = verify(
        VerificationRequest(
            files=(FileClaim("artifact.txt"),),
            working_directory=tmp_path,
        )
    )

    assert result.accepted
    assert result.deterministic.findings[0].status is FindingStatus.PASSED


def test_git_root_relative_file_does_not_produce_missing_false_positive(
    tmp_path: Path,
) -> None:
    (tmp_path / ".git").mkdir()
    root_file = tmp_path / "pyproject.toml"
    root_file.write_text("[project]", encoding="utf-8")
    working_directory = tmp_path / "src" / "nested"
    working_directory.mkdir(parents=True)

    resolved = resolve_claimed_file(
        "pyproject.toml",
        working_directory=working_directory,
    )
    result = verify(
        VerificationRequest(
            files=(FileClaim("pyproject.toml"),),
            working_directory=working_directory,
        )
    )

    assert resolved == root_file.resolve()
    assert result.accepted
    assert "not found" not in result.deterministic.findings[0].detail


def test_missing_file_is_a_blocking_deterministic_failure(tmp_path: Path) -> None:
    result = verify(
        VerificationRequest(
            files=(FileClaim("missing.txt"),),
            working_directory=tmp_path,
            repository_root=tmp_path,
        )
    )

    assert not result.accepted
    assert result.deterministic.findings[0].status is FindingStatus.FAILED
    assert result.deterministic.state is ExecutionState.COMPLETED


def test_command_exit_zero_passes_and_nonzero_blocks(tmp_path: Path) -> None:
    passed = verify(
        VerificationRequest(
            commands=(CommandClaim((sys.executable, "-c", "raise SystemExit(0)")),),
            working_directory=tmp_path,
        )
    )
    failed = verify(
        VerificationRequest(
            commands=(CommandClaim((sys.executable, "-c", "raise SystemExit(7)")),),
            working_directory=tmp_path,
        )
    )

    assert passed.accepted
    assert passed.deterministic.findings[0].detail == "exit code 0"
    assert not failed.accepted
    assert failed.deterministic.findings[0].detail == "exit code 7"


def test_advisory_failure_never_blocks_deterministic_acceptance(
    tmp_path: Path,
) -> None:
    result = verify(
        VerificationRequest(
            advisory_checks=(lambda: False,),
            working_directory=tmp_path,
        )
    )

    assert result.advisory.findings[0].status is FindingStatus.FAILED
    assert not result.advisory.passed
    assert result.accepted


def test_advisory_findings_are_normalized_to_the_advisory_phase(
    tmp_path: Path,
) -> None:
    def opinion() -> VerificationFinding:
        return VerificationFinding(
            VerificationPhase.DETERMINISTIC,
            "quality",
            FindingStatus.FAILED,
            "subjective concern",
        )

    result = verify(
        VerificationRequest(
            advisory_checks=(opinion,),
            working_directory=tmp_path,
        )
    )

    assert result.advisory.findings[0].phase is VerificationPhase.ADVISORY
    assert result.accepted


def test_each_phase_has_independent_lifecycle_metadata(tmp_path: Path) -> None:
    result = verify(
        VerificationRequest(
            working_directory=tmp_path,
            deterministic_deadlines=ExecutionDeadlines(
                absolute_timeout_s=2.0,
                idle_timeout_s=1.0,
            ),
            advisory_deadlines=ExecutionDeadlines(
                absolute_timeout_s=4.0,
                idle_timeout_s=3.0,
            ),
            execution_id="verification-execution",
        )
    )

    deterministic = result.deterministic.execution
    advisory = result.advisory.execution
    assert deterministic["execution_id"] == advisory["execution_id"]
    assert deterministic["attempt_id"] != advisory["attempt_id"]
    assert deterministic["absolute_deadline_s"] == 2.0
    assert deterministic["idle_deadline_s"] == 1.0
    assert advisory["absolute_deadline_s"] == 4.0
    assert advisory["idle_deadline_s"] == 3.0
    assert deterministic["termination_confirmed"] is True
    assert advisory["termination_confirmed"] is True
    assert deterministic["cancellation_requested"] is False
    assert advisory["cancellation_requested"] is False


def test_cancellation_is_recorded_and_termination_is_confirmed(tmp_path: Path) -> None:
    control = CancellationToken()
    control.request_cancel("user_requested")

    result = verify(
        VerificationRequest(working_directory=tmp_path),
        control=control,
    )

    assert result.deterministic.state is ExecutionState.CANCELLED
    assert result.advisory.state is ExecutionState.CANCELLED
    assert result.deterministic.execution["reason"] == "user_requested"
    assert result.deterministic.termination_confirmed
    assert result.advisory.termination_confirmed
    assert not result.accepted


def test_command_deadline_terminates_and_confirms_the_phase(tmp_path: Path) -> None:
    result = verify(
        VerificationRequest(
            commands=(
                CommandClaim((sys.executable, "-c", "import time; time.sleep(2)")),
            ),
            working_directory=tmp_path,
            deterministic_deadlines=ExecutionDeadlines(absolute_timeout_s=0.05),
        )
    )

    assert result.deterministic.state is ExecutionState.TIMED_OUT
    assert result.deterministic.termination_confirmed
    assert not result.accepted


def test_advisory_deadline_is_non_blocking(tmp_path: Path) -> None:
    def slow_opinion() -> bool:
        time.sleep(0.02)
        return False

    result = verify(
        VerificationRequest(
            advisory_checks=(slow_opinion,),
            working_directory=tmp_path,
            advisory_deadlines=ExecutionDeadlines(absolute_timeout_s=0.005),
        )
    )

    assert result.advisory.state is ExecutionState.TIMED_OUT
    assert result.advisory.termination_confirmed
    assert result.accepted


def test_verifier_imports_no_other_athena_package_than_execution() -> None:
    package = Path(__file__).resolve().parents[1] / "athena" / "verifier"
    imports: set[str] = set()

    for module in package.glob("*.py"):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        imports.update(
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level == 0
        )
        imports.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )

    athena_imports = {
        name for name in imports if name == "athena" or name.startswith("athena.")
    }
    assert athena_imports == {"athena.execution"}
