"""Implementação do pipeline determinístico/advisory."""

from __future__ import annotations

import subprocess
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from athena.execution import (
    CancellationToken,
    ExecutionControl,
    ExecutionDeadlines,
    ExecutionRecord,
    ExecutionState,
)

from .contracts import (
    CommandClaim,
    FindingStatus,
    VerificationFinding,
    VerificationPhase,
    VerificationPhaseResult,
    VerificationRequest,
    VerificationResult,
)

_POLL_INTERVAL_S = 0.01
_TERMINATION_GRACE_S = 1.0


def verify(
    request: VerificationRequest,
    *,
    control: ExecutionControl | None = None,
) -> VerificationResult:
    """Executar fatos objetivos antes de pareceres não bloqueantes."""
    shared_control = control or CancellationToken()
    execution_id = request.execution_id or str(uuid.uuid4())
    deterministic = _run_deterministic(request, shared_control, execution_id)
    advisory = _run_advisory(request, shared_control, execution_id)
    return VerificationResult(deterministic=deterministic, advisory=advisory)


def resolve_claimed_file(
    claimed_path: str | Path,
    *,
    working_directory: str | Path | None = None,
    repository_root: str | Path | None = None,
) -> Path | None:
    """Resolver uma alegação absoluta ou relativa à raiz e ao diretório de trabalho."""
    path = Path(claimed_path).expanduser()
    if path.is_absolute():
        return path.resolve() if path.is_file() else None

    working = Path(working_directory or Path.cwd()).expanduser().resolve()
    root = (
        Path(repository_root).expanduser().resolve()
        if repository_root is not None
        else _find_repository_root(working)
    )
    candidates = (root / path, working / path)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def _find_repository_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return start


def _new_record(
    phase: VerificationPhase,
    deadlines: ExecutionDeadlines,
    execution_id: str,
) -> ExecutionRecord:
    return ExecutionRecord(
        f"verifier:{phase.value}",
        profile="verification",
        execution_id=execution_id,
        attempt_id=str(uuid.uuid4()),
        deadlines=deadlines,
    )


def _run_deterministic(
    request: VerificationRequest,
    control: ExecutionControl,
    execution_id: str,
) -> VerificationPhaseResult:
    phase = VerificationPhase.DETERMINISTIC
    record = _new_record(phase, request.deterministic_deadlines, execution_id)
    findings: list[VerificationFinding] = []
    record.transition(ExecutionState.STARTING)
    terminal = _requested_terminal(record, control)
    if terminal is not None:
        return _finish(phase, findings, record, terminal, control)
    record.transition(ExecutionState.RUNNING)

    for claim in request.files:
        terminal = _requested_terminal(record, control)
        if terminal is not None:
            return _finish(phase, findings, record, terminal, control)
        resolved = resolve_claimed_file(
            claim.path,
            working_directory=request.working_directory,
            repository_root=request.repository_root,
        )
        findings.append(
            VerificationFinding(
                phase,
                str(claim.path),
                FindingStatus.PASSED if resolved is not None else FindingStatus.FAILED,
                str(resolved) if resolved is not None else "claimed file was not found",
            )
        )
        record.record_progress()

    command_directory = Path(
        request.working_directory or request.repository_root or Path.cwd()
    ).expanduser().resolve()
    for claim in request.commands:
        terminal = _requested_terminal(record, control)
        if terminal is not None:
            return _finish(phase, findings, record, terminal, control)
        finding, terminal, termination_confirmed = _check_command(
            claim,
            command_directory,
            record,
            control,
        )
        if finding is not None:
            findings.append(finding)
        if terminal is not None:
            return _finish(
                phase,
                findings,
                record,
                terminal,
                control,
                termination_confirmed=termination_confirmed,
            )
        record.record_progress()

    return _finish(phase, findings, record, ExecutionState.COMPLETED, control)


def _check_command(
    claim: CommandClaim,
    directory: Path,
    record: ExecutionRecord,
    control: ExecutionControl,
) -> tuple[VerificationFinding | None, ExecutionState | None, bool]:
    subject = " ".join(claim.command)
    try:
        process = subprocess.Popen(
            claim.command,
            cwd=directory,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        return (
            VerificationFinding(
                VerificationPhase.DETERMINISTIC,
                subject,
                FindingStatus.FAILED,
                f"command could not start: {type(exc).__name__}",
            ),
            None,
            True,
        )

    while process.poll() is None:
        terminal = _requested_terminal(record, control)
        if terminal is not None:
            confirmed = _terminate_process(process)
            return None, terminal if confirmed else ExecutionState.TERMINATION_UNCONFIRMED, confirmed
        time.sleep(_POLL_INTERVAL_S)

    return (
        VerificationFinding(
            VerificationPhase.DETERMINISTIC,
            subject,
            FindingStatus.PASSED if process.returncode == 0 else FindingStatus.FAILED,
            f"exit code {process.returncode}",
        ),
        None,
        True,
    )


def _terminate_process(process: subprocess.Popen[bytes]) -> bool:
    if process.poll() is not None:
        return True
    process.terminate()
    try:
        process.wait(timeout=_TERMINATION_GRACE_S)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=_TERMINATION_GRACE_S)
        except subprocess.TimeoutExpired:
            return False
    return process.poll() is not None


def _run_advisory(
    request: VerificationRequest,
    control: ExecutionControl,
    execution_id: str,
) -> VerificationPhaseResult:
    phase = VerificationPhase.ADVISORY
    record = _new_record(phase, request.advisory_deadlines, execution_id)
    findings: list[VerificationFinding] = []
    record.transition(ExecutionState.STARTING)
    terminal = _requested_terminal(record, control)
    if terminal is not None:
        return _finish(phase, findings, record, terminal, control)
    record.transition(ExecutionState.RUNNING)

    for index, check in enumerate(request.advisory_checks):
        terminal = _requested_terminal(record, control)
        if terminal is not None:
            return _finish(phase, findings, record, terminal, control)
        findings.append(_call_advisory(check, index))
        terminal = _requested_terminal(record, control)
        if terminal is not None:
            return _finish(phase, findings, record, terminal, control)
        record.record_progress()

    return _finish(phase, findings, record, ExecutionState.COMPLETED, control)


def _call_advisory(
    check: Callable[[], VerificationFinding | bool | None],
    index: int,
) -> VerificationFinding:
    subject = f"advisory-{index + 1}"
    try:
        outcome = check()
    except Exception as exc:  # noqa: BLE001 - advisory externo é isolado.
        return VerificationFinding(
            VerificationPhase.ADVISORY,
            subject,
            FindingStatus.FAILED,
            f"advisory check raised {type(exc).__name__}",
        )
    if isinstance(outcome, VerificationFinding):
        return VerificationFinding(
            VerificationPhase.ADVISORY,
            outcome.subject,
            outcome.status,
            outcome.detail,
        )
    if outcome is None:
        return VerificationFinding(
            VerificationPhase.ADVISORY,
            subject,
            FindingStatus.SKIPPED,
            "no opinion",
        )
    return VerificationFinding(
        VerificationPhase.ADVISORY,
        subject,
        FindingStatus.PASSED if outcome else FindingStatus.FAILED,
        "advisory opinion",
    )


def _requested_terminal(
    record: ExecutionRecord,
    control: ExecutionControl,
) -> ExecutionState | None:
    if control.cancellation_requested:
        return ExecutionState.CANCELLED
    if record.expired_deadline() is not None:
        return ExecutionState.TIMED_OUT
    return None


def _finish(
    phase: VerificationPhase,
    findings: list[VerificationFinding],
    record: ExecutionRecord,
    state: ExecutionState,
    control: ExecutionControl,
    *,
    termination_confirmed: bool = True,
) -> VerificationPhaseResult:
    reason = control.cancel_reason if state is ExecutionState.CANCELLED else None
    record.transition(state, reason=reason)
    execution: dict[str, object] = record.to_dict()
    execution["cancellation_requested"] = control.cancellation_requested
    execution["termination_confirmed"] = termination_confirmed
    return VerificationPhaseResult(
        phase=phase,
        findings=tuple(findings),
        execution=execution,
        termination_confirmed=termination_confirmed,
    )
