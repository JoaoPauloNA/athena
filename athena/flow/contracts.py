"""Contratos do adaptador FLOW-1 injetado no MCPServer.

Separado do controller.py para evitar importações circulares:
mcp_server importa somente este módulo (via mcp_runtime); o controller
concreto é importado apenas por mcp_runtime e athena.flow.__init__.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class FlowControllerContract(Protocol):
    """Adaptador FLOW-1: compõe TASK-0, Evidence Gate e Chronos.

    Chamado pelo MCPServer apenas quando task_handle está presente.
    Nunca importa SQLite, Evidence Gate ou Chronos diretamente — esses
    detalhes são responsabilidade da implementação concreta.
    """

    def begin(self, task_handle: str, execution_id: str) -> None:
        """Transição atômica queued→running antes do runner.

        Lança TaskHandleNotFound, TaskNotExecutable ou TaskStoreUnavailable.
        """
        ...

    def finish(
        self,
        task_handle: str,
        execution_id: str,
        execution_result: Any,
        verification_result: Any,
    ) -> dict[str, Any]:
        """Emitir Evidence Gate, Chronos e persistir projeção terminal.

        Retorna payload sanitizado com campos FLOW permitidos.
        Lança TaskStoreUnavailable se persistência falhar.
        """
        ...

    def close_failed(
        self,
        task_handle: str,
        execution_id: str,
        reason_codes: tuple[str, ...],
    ) -> None:
        """Fechar durável em awaiting_human_review após falha antes ou durante runner.

        Idempotente: se já terminal, não faz nada.
        Nunca persiste texto de exceção ou dados brutos.
        """
        ...
