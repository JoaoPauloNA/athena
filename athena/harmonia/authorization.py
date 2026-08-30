"""Autorização de selo — produção falha fechado; testes usam fake determinístico."""

from __future__ import annotations

import hashlib
import json

from .contracts import IsolationStrategy, SubtaskSpec


class DenySealAuthorizer:
    """Implementação padrão: nenhum selo é autorizado."""

    def authorize_subtask(
        self,
        *,
        task_id: str,
        subtask: SubtaskSpec,
        read_paths: tuple[str, ...],
        write_paths: tuple[str, ...],
        isolation: IsolationStrategy,
    ) -> bool:
        return False


class DenyScopeEnforcementAuthority:
    """Implementação padrão: escopo paralelo nunca comprovado sem worktree."""

    def proves_exact_parallel_scope(
        self,
        *,
        task_id: str,
        subtask: SubtaskSpec,
        read_paths: tuple[str, ...],
        write_paths: tuple[str, ...],
    ) -> bool:
        return False


class DeterministicFakeAuthorizer:
    """Autorizador determinístico para testes — hash do plano exato."""

    def __init__(self, *, authorized_seals: frozenset[str] | None = None) -> None:
        self._authorized = authorized_seals

    @staticmethod
    def seal_for(
        *,
        task_id: str,
        subtask: SubtaskSpec,
        read_paths: tuple[str, ...],
        write_paths: tuple[str, ...],
        isolation: IsolationStrategy,
    ) -> str:
        payload = {
            "task_id": task_id,
            "subtask_id": subtask.subtask_id,
            "read_paths": read_paths,
            "write_paths": write_paths,
            "isolation": isolation.value,
            "resources": {
                "cpu_tokens": subtask.resources.cpu_tokens,
                "ram_mb": subtask.resources.ram_mb,
                "gpu_tokens": subtask.resources.gpu_tokens,
                "provider_tokens": subtask.resources.provider_tokens,
            },
            "operation_type": subtask.operation_type,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return digest

    def authorize_subtask(
        self,
        *,
        task_id: str,
        subtask: SubtaskSpec,
        read_paths: tuple[str, ...],
        write_paths: tuple[str, ...],
        isolation: IsolationStrategy,
    ) -> bool:
        candidates = {isolation, IsolationStrategy.GRANULAR_LEASE, IsolationStrategy.WORKTREE}
        for candidate in candidates:
            expected = self.seal_for(
                task_id=task_id,
                subtask=subtask,
                read_paths=read_paths,
                write_paths=write_paths,
                isolation=candidate,
            )
            if subtask.seal_hash == expected:
                if self._authorized is not None:
                    return subtask.seal_hash in self._authorized
                return True
        return False
