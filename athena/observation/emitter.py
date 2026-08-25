"""Observação sombra opt-in (MO-2): ponte sanitizada Athena → observer externo.

Ativação: env `ATHENA_MOIRAS_SHADOW=1` + observer injetado na composição
(`mcp_runtime.py`). Sem os dois, zero custo e zero acoplamento.
O observer nunca lança; falhas dele são engolidas e contadas — observação
não pode derrubar execução.
"""

from __future__ import annotations

import os

from athena.execution import ExecutionRecord, ExecutionState

from .contracts import ShadowExecutionEvent, ShadowObserverContract

ENV_FLAG = "ATHENA_MOIRAS_SHADOW"


def shadow_enabled(env: dict[str, str] | None = None) -> bool:
    source = env if env is not None else os.environ
    return source.get(ENV_FLAG, "").strip() == "1"


class _SilentObserver:
    """Wrapper que garante a fronteira: observer falho nunca propaga."""

    def __init__(self, inner: ShadowObserverContract) -> None:
        self._inner = inner
        self.suppressed_errors = 0

    def observe(self, event: ShadowExecutionEvent) -> None:
        try:
            self._inner.observe(event)
        except Exception:  # noqa: BLE001 - observação não pode derrubar execução
            self.suppressed_errors += 1


class ShadowEmitter:
    """Emitir snapshots sanitizados nos marcos do ciclo de vida."""

    def __init__(
        self,
        observer: ShadowObserverContract | None = None,
        *,
        enabled: bool | None = None,
    ) -> None:
        self._enabled = shadow_enabled() if enabled is None else enabled
        self._observer = (
            _SilentObserver(observer) if (observer is not None and self._enabled) else None
        )

    @property
    def active(self) -> bool:
        return self._observer is not None

    @property
    def suppressed_errors(self) -> int:
        return self._observer.suppressed_errors if self._observer else 0

    def emit_transition(
        self,
        record: ExecutionRecord,
        state: ExecutionState,
        *,
        cancelled_by_client: bool = False,
    ) -> None:
        observer = self._observer
        if observer is None or record is None:
            return
        try:
            snapshot = record.to_dict()
        except Exception:  # noqa: BLE001 - snapshot indisponível: não emitir
            return
        event = ShadowExecutionEvent(
            execution_id=str(snapshot.get("execution_id", "")),
            attempt_id=str(snapshot.get("attempt_id", "")),
            provider=str(snapshot.get("provider", "")),
            state=state.value,
            progress_counter=int(snapshot.get("progress_counter", 0) or 0),
            duration_s=snapshot.get("duration_s"),
            expired_deadline=snapshot.get("expired_deadline"),
            cancelled_by_client=cancelled_by_client,
        )
        # to_dict valida a allowlist antes de qualquer entrega
        event.to_dict()
        observer.observe(event)
