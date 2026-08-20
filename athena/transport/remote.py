"""Execução remota conservadora por meio de runner injetado."""

from __future__ import annotations

from athena.execution import ExecutionState

from .contracts import RemoteExecutionResult, RemoteProcessOutcome, RemoteRunner


class RemoteExecutor:
    """Executar argv remotamente sem inferir término pelo processo SSH local."""

    def __init__(self, runner: RemoteRunner) -> None:
        if not isinstance(runner, RemoteRunner):
            raise TypeError("runner must implement RemoteRunner")
        self._runner = runner

    def execute(
        self, argv: tuple[str, ...], *, timeout_s: float | None = None
    ) -> RemoteExecutionResult:
        """Retornar sempre término não confirmado para uma sessão remota."""
        if not argv:
            raise ValueError("argv must not be empty")
        if timeout_s is not None and timeout_s <= 0:
            raise ValueError("timeout_s must be greater than zero")
        try:
            outcome = self._runner.run(argv, timeout_s=timeout_s)
        except TimeoutError:
            outcome = RemoteProcessOutcome(return_code=None, timed_out=True)
        return RemoteExecutionResult(
            argv=argv,
            state=ExecutionState.TERMINATION_UNCONFIRMED,
            return_code=outcome.return_code,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            timed_out=outcome.timed_out,
        )


def execute_remote(
    argv: tuple[str, ...],
    *,
    runner: RemoteRunner,
    timeout_s: float | None = None,
) -> RemoteExecutionResult:
    """Atalho funcional para executar por um runner substituível."""
    return RemoteExecutor(runner).execute(argv, timeout_s=timeout_s)
