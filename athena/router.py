"""Router do Athena-MCP: lógica de failover entre providers via combos."""
from __future__ import annotations

import math
import time
import uuid
from collections.abc import Callable
from copy import deepcopy

from athena import workspace_lease
from athena.bridge import RunResult
from athena.combos import ComboStep, get_combo
from athena.execution import DeadlineBudget, ExecutionControl, ExecutionState
from athena.orchestrator_escalation import (
    apply_orchestrator_continuation,
    classify_verifiable_failure,
    record_failure_and_maybe_escalate,
)
from athena.providers import PROVIDERS, ask_provider
from athena.reinforced_verification import (
    ReinforcedVerificationContext,
    emit_reinforced_verification_event,
    evaluate_reinforced_verification,
)
from athena.service_profiles import (
    get_service_profile,
    resolve_service_profile,
    resolve_timeouts,
)


class AllProvidersFailed(Exception):
    """Todos os providers do combo falharam."""


class FallbackBlocked(AllProvidersFailed):
    """Fallback/retry impedido por segurança do ciclo de vida (Sprint A5).

    Levantada quando o metadado de execução (`RunResult.execution`) da
    tentativa anterior indica que ela pode ainda estar ativa, ou que sua
    terminação não foi confirmada. Nesses casos iniciar uma segunda
    tentativa (mesmo provider ou o próximo do combo) arriscaria dois
    processos concorrentes fazendo o mesmo trabalho, então o router se
    recusa a prosseguir em vez de arriscar uma corrida.

    É subclasse de `AllProvidersFailed` para que `except AllProvidersFailed`
    existente continue capturando este caso; código que precisa distinguir
    "esgotou o combo normalmente" de "bloqueado por segurança" pode capturar
    `FallbackBlocked` especificamente.
    """


class ComboDeadlineExceeded(AllProvidersFailed):
    """Combo-level overall deadline exhausted before next safe stage."""


class OrchestratorEscalationRequired(AllProvidersFailed):
    """Falhas verificáveis exigem decisão do orquestrador externo.

    Subclasse de ``AllProvidersFailed`` para compatibilidade com capturas
    existentes; use ``.package`` para o evento ``orchestrator_escalation_required``.
    """

    def __init__(self, package: dict) -> None:
        self.package = package
        super().__init__(package.get("summary") or "orchestrator_escalation_required")


_TERMINAL_EXECUTION_STATES = frozenset(
    {
        ExecutionState.FAILED.value,
        ExecutionState.CANCELLED.value,
        ExecutionState.TIMED_OUT.value,
    }
)


def _is_safe_terminal_metadata(
    execution: dict | None,
    *,
    expected_execution_id: str | None = None,
    expected_attempt_id: str | None = None,
) -> tuple[bool, str | None]:
    """Checks whether execution metadata represents a safely terminated phase."""
    if not execution:
        return False, "metadados de execução ausentes"
    if expected_execution_id is not None and execution.get("execution_id") != expected_execution_id:
        return False, "execution_id do metadado não corresponde à execução titular da lease"
    if expected_attempt_id is not None and execution.get("attempt_id") != expected_attempt_id:
        return False, "attempt_id do metadado não corresponde à tentativa titular da lease"
    if execution.get("client_abandoned"):
        return False, "cliente abandonou a execução anterior; política não autoriza fallback explicitamente"
    if (
        execution.get("remote_session_started") is True
        and execution.get("remote_termination_confirmed") is not True
    ):
        return (
            False,
            "sessão remota iniciada sem confirmação explícita de terminação remota",
        )
    state = execution.get("state")
    if state == ExecutionState.TERMINATION_UNCONFIRMED.value:
        return False, "terminação da tentativa anterior não confirmada (TERMINATION_UNCONFIRMED)"
    if state == ExecutionState.COMPLETED.value and execution.get("process_created") is False:
        return True, None
    if state in _TERMINAL_EXECUTION_STATES or state == ExecutionState.COMPLETED.value:
        if state == ExecutionState.FAILED.value and execution.get("process_created") is False:
            return True, None
        if state == ExecutionState.CANCELLED.value and execution.get("process_created") is False:
            return True, None
        if state == ExecutionState.TIMED_OUT.value and execution.get("process_created") is False:
            return True, None
        if not execution.get("direct_process_terminated_confirmed"):
            return False, f"terminação do processo direto não confirmada (estado {state})"
        if execution.get("pgid") is not None and not execution.get("process_tree_terminated_confirmed"):
            return False, f"terminação da árvore de processos não confirmada (estado {state})"
        return True, None
    return False, f"tentativa anterior pode ainda estar ativa (estado {state})"


def _fallback_block_reason(
    result: RunResult,
    *,
    require_execution_metadata: bool = False,
    expected_execution_id: str | None = None,
    expected_attempt_id: str | None = None,
) -> str | None:
    """Decide se é seguro iniciar uma nova tentativa após `result`.

    Conservador por padrão: só libera o fallback quando os metadados de
    execução confirmam positivamente que a tentativa anterior terminou.

    - `result.execution is None` (RunResult legado/mockado, sem contrato de
      execução coletado): preserva o comportamento pré-A5 — não há como
      avaliar confirmação de terminação, então a decisão fica inteiramente
      com a política de failover existente (on_timeout/on_error).
    - `client_abandoned`: bloqueia sempre. Não há política explícita para
      reautorizar fallback após abandono do cliente, então o padrão seguro é
      recusar.
    - `TERMINATION_UNCONFIRMED`: bloqueia sempre — por definição a
      terminação não pôde ser verificada.
    - `COMPLETED`: bloqueia — a tentativa já teve sucesso, uma nova tentativa
      não deveria ser iniciada (o caminho de sucesso do router já retorna
      antes de chegar aqui; isto é apenas defesa em profundidade).
    - `FAILED` com `process_created` explicitamente `False`: libera sempre,
      sem exigir confirmação de terminação. Esse caso é o de
      FileNotFoundError/OSError levantado por `subprocess.Popen()` antes que
      qualquer processo real chegasse a existir (ex.: comando inexistente) —
      não há processo para confirmar como terminado, então exigir essa
      confirmação bloquearia o fallback para sempre.
    - `FAILED` / `CANCELLED` / `TIMED_OUT` (demais casos, `process_created`
      `True` ou ausente): libera somente se a terminação do processo direto
      está confirmada e, quando havia um grupo de processos (`pgid`
      presente), a terminação da árvore também está confirmada. A decisão
      final de fazer ou não o fallback continua com a política do combo
      (on_timeout/on_error) — este helper só remove a opção quando seria
      inseguro, nunca força um fallback que a política já recusa.
    - Qualquer outro estado (QUEUED/STARTING/RUNNING/CANCELLATION_REQUESTED/
      TERMINATING): bloqueia — a tentativa anterior pode ainda estar ativa.
    """
    execution = result.execution
    if not execution:
        if require_execution_metadata:
            return "metadados de execução ausentes; fallback fail-closed com lease ativa"
        return None
    safe, reason = _is_safe_terminal_metadata(
        execution,
        expected_execution_id=expected_execution_id,
        expected_attempt_id=expected_attempt_id,
    )
    if execution.get("state") == ExecutionState.COMPLETED.value:
        return "tentativa anterior já completou (COMPLETED)"
    if safe:
        return None
    return reason


def run_combo(
    combo_id: str,
    prompt: str,
    *,
    working_directory: str | None = None,
    timeout: int | None = None,
    heavy_model_authorized: bool = False,
    task_type: str | None = None,
    verify: bool = False,
    verification_timeout: float | None = None,
    overall_timeout: float | None = None,
    execution_id: str | None = None,
    on_execution_update: Callable[[dict], None] | None = None,
    execution_control: ExecutionControl | None = None,
    service_profile: str | None = None,
    idle_timeout: float | None = None,
    orchestrator_continuation: dict | None = None,
) -> RunResult:
    """Executa um prompt através de um combo, com fallback condicionado.

    Percorre a chain do combo em ordem. Se um provider falha (timeout, erro),
    tenta o próximo da lista conforme a política de failover.

    Com verify=True, o relatório de cada provider passa pelo verificador
    (determinístico primeiro, advisory depois). Relatório marcado FALSO
    conta como falha para a política e só permite o próximo provider quando
    o perfil e a confirmação de término autorizam a transição.
    """
    combo = get_combo(combo_id)
    if combo is None:
        raise ValueError(f"Combo '{combo_id}' não encontrado")
    if not combo.enabled:
        raise ValueError(f"Combo '{combo_id}' está desabilitado")
    if not combo.chain:
        raise ValueError(f"Combo '{combo_id}' não tem providers configurados")

    execution_lease_id = execution_id or uuid.uuid4().hex
    continuation_opts = apply_orchestrator_continuation(
        execution_lease_id,
        orchestrator_continuation,
    )
    active_chain = list(combo.chain)
    if continuation_opts and continuation_opts.get("chain_provider_ids"):
        override_ids = continuation_opts["chain_provider_ids"]
        active_chain = [
            ComboStep(provider_id=provider_id)
            for provider_id in override_ids
        ]

    if overall_timeout is not None and (
        isinstance(overall_timeout, bool)
        or float(overall_timeout) <= 0
        or not math.isfinite(float(overall_timeout))
    ):
        raise ValueError("overall_timeout deve ser número positivo finito")

    primary_provider_id = combo.chain[0].provider_id
    resolved_profile = resolve_service_profile(
        explicit_profile_id=service_profile,
        provider_id=primary_provider_id,
        task_type=task_type,
        working_directory=working_directory,
    )
    if resolved_profile.requires_workspace and not working_directory:
        raise ValueError(
            f"service_profile '{resolved_profile.id}' exige working_directory"
        )
    verification_profile = get_service_profile("verification")
    verification_base_timeout, _ = resolve_timeouts(
        profile=verification_profile,
        provider_default_absolute_timeout_s=verification_profile.max_absolute_timeout_s,
        explicit_absolute_timeout_s=verification_timeout,
        explicit_idle_timeout_s=None,
    )
    if verification_base_timeout is None:
        raise ValueError(
            "service_profile 'verification' não possui timeout absoluto efetivo"
        )

    pre_resolved_step_timeouts: list[tuple[float | None, float | None]] = []
    for step in active_chain:
        provider_spec = PROVIDERS.get(step.provider_id)
        explicit_absolute_timeout = timeout if timeout is not None else step.timeout
        pre_resolved_step_timeouts.append(
            resolve_timeouts(
                profile=resolved_profile,
                provider_default_absolute_timeout_s=(
                    provider_spec.default_timeout if provider_spec is not None else None
                ),
                explicit_absolute_timeout_s=explicit_absolute_timeout,
                explicit_idle_timeout_s=idle_timeout,
            )
        )

    start_time = time.monotonic()
    overall_budget = (
        DeadlineBudget(overall_timeout) if overall_timeout is not None else None
    )
    last_error: str | None = None
    attempted: list[str] = []

    # whole run_combo() call as the owning execution; each provider attempt
    # gets its own attempt_lease_id, acquired/transferred before that
    # attempt's ask_provider() call so a second execution can never touch
    # the same canonical workspace while this one might still be active.
    canonical_workspace = (
        workspace_lease.canonicalize_workspace(working_directory) if working_directory else None
    )
    lease_acquired = False
    current_attempt_lease_id: str | None = None
    current_attempt_started = False
    last_execution_meta: dict | None = None

    def _logical_timeout_meta(execution_id_value: str, attempt_id_value: str) -> dict:
        return {
            "execution_id": execution_id_value,
            "attempt_id": attempt_id_value,
            "provider": "router",
            "profile": "combo",
            "state": ExecutionState.TIMED_OUT.value,
            "process_created": False,
            "termination_reason": "combo_overall_deadline",
            "direct_process_terminated_confirmed": False,
            "process_tree_terminated_confirmed": False,
            "client_abandoned": False,
        }

    def _raise_combo_deadline() -> None:
        nonlocal last_execution_meta
        if (
            canonical_workspace is not None
            and lease_acquired
            and current_attempt_lease_id is not None
        ):
            safe, _ = _is_safe_terminal_metadata(
                last_execution_meta,
                expected_execution_id=execution_lease_id,
                expected_attempt_id=current_attempt_lease_id,
            )
            if safe:
                _lease_release(last_execution_meta)
            elif not current_attempt_started:
                logical_meta = _logical_timeout_meta(
                    execution_lease_id, current_attempt_lease_id
                )
                _lease_release(logical_meta)
                last_execution_meta = logical_meta
        attempted_text = ", ".join(attempted) if attempted else "(nenhum provider iniciado)"
        raise ComboDeadlineExceeded(
            f"Combo '{combo_id}' excedeu overall_timeout antes de iniciar novo estágio. "
            f"Tentados: {attempted_text}. "
            f"(duração total: {time.monotonic() - start_time:.1f}s)."
        )

    def _lease_acquire_or_transfer() -> None:
        nonlocal lease_acquired
        if canonical_workspace is None:
            return
        nonlocal current_attempt_lease_id, current_attempt_started
        attempt_lease_id = uuid.uuid4().hex
        if not lease_acquired:
            workspace_lease.acquire(canonical_workspace, execution_lease_id, attempt_lease_id)
            lease_acquired = True
        else:
            workspace_lease.transfer(
                canonical_workspace,
                execution_lease_id,
                current_attempt_lease_id or "",
                attempt_lease_id,
                execution_meta=last_execution_meta,
            )
        current_attempt_lease_id = attempt_lease_id
        current_attempt_started = False

    def _lease_release(execution_meta: dict | None) -> None:
        if canonical_workspace is None or not lease_acquired or current_attempt_lease_id is None:
            return
        workspace_lease.release_after_attempt(
            canonical_workspace,
            execution_lease_id,
            current_attempt_lease_id,
            execution_meta=execution_meta,
        )

    def _capture_execution_update(attempt_snapshot: dict) -> None:
        """Keep a trusted local copy before forwarding the lifecycle event."""
        nonlocal last_execution_meta
        if isinstance(attempt_snapshot, dict):
            last_execution_meta = deepcopy(attempt_snapshot)
        if on_execution_update is not None:
            on_execution_update(attempt_snapshot)

    def _release_after_authenticated_terminal_snapshot() -> None:
        """Release after an exception only when the current attempt is proven over."""
        safe, _ = _is_safe_terminal_metadata(
            last_execution_meta,
            expected_execution_id=execution_lease_id if canonical_workspace else None,
            expected_attempt_id=current_attempt_lease_id if canonical_workspace else None,
        )
        if safe:
            _lease_release(last_execution_meta)

    def _budget_remaining() -> float | None:
        return overall_budget.remaining if overall_budget is not None else None

    def _record_and_maybe_escalate(
        *,
        result: RunResult,
        provider_id: str,
        last_error_value: str | None,
        false_verdict: bool = False,
        unconfirmed_termination: bool = False,
    ) -> None:
        failure = classify_verifiable_failure(
            provider_id=provider_id,
            result=result,
            last_error=last_error_value,
            false_verdict=false_verdict,
            unconfirmed_termination=unconfirmed_termination,
            execution_id=execution_lease_id,
        )
        if failure is None:
            return
        package = record_failure_and_maybe_escalate(
            execution_id=execution_lease_id,
            combo_id=combo_id,
            attempted_chain=attempted,
            failure=failure,
            budget_remaining_s=_budget_remaining(),
            episode_id=execution_lease_id,
        )
        if package is not None:
            raise OrchestratorEscalationRequired(package)

    # Cadeia declarada no combo, antes de qualquer override de continuação.
    # É a referência de "cadeia original" da política de verificação reforçada.
    original_chain_ids = tuple(chain_step.provider_id for chain_step in combo.chain)
    standard_provider_ids = (primary_provider_id,) if original_chain_ids else ()

    def _annotate_reinforced_verification(
        result: RunResult,
        *,
        step: ComboStep,
        step_index: int,
    ) -> None:
        """Anexa a decisão de verificação reforçada sem alterar o resultado.

        Aditivo por construção: escreve apenas `result.reinforced_verification`
        e um aviso legível em `result.warnings`. Nunca toca em exit_code,
        output, verdict ou política de fallback.
        """
        from athena.models import classify_weight
        from athena.recommend import estimate_complexity

        model = step.model
        context = ReinforcedVerificationContext(
            used_provider_id=step.provider_id,
            used_model=model,
            used_weight=classify_weight(model) if model else None,
            task_complexity=estimate_complexity(prompt),
            standard_provider_ids=standard_provider_ids,
            original_chain=original_chain_ids,
            is_fallback=step_index > 0,
            service_profile_id=resolved_profile.id,
        )
        decision = evaluate_reinforced_verification(context)
        result.reinforced_verification = decision.to_dict()
        if not decision.requires_reinforced_verification:
            return
        result.warnings.append(
            "Verificação reforçada exigida para o resultado de "
            f"'{step.provider_id}': {', '.join(decision.reasons)}."
        )
        emit_reinforced_verification_event(
            decision,
            execution_id=execution_lease_id,
            combo_id=combo_id,
            provider_id=step.provider_id,
            attempted_chain=attempted,
            original_chain=list(original_chain_ids),
        )

    for step_index, step in enumerate(active_chain):
        if overall_budget is not None and overall_budget.expired:
            _raise_combo_deadline()
        provider_id = step.provider_id
        attempted.append(provider_id)

        for attempt in range(combo.failover_policy.max_retries_per_provider):
            if overall_budget is not None and overall_budget.expired:
                _raise_combo_deadline()
            # Acquire before the provider starts (or transfer from the prior
            # attempt of this same execution, gated on its confirmed
            # termination) -- never after.
            _lease_acquire_or_transfer()

            provider_timeout, provider_idle_timeout = pre_resolved_step_timeouts[step_index]
            if overall_budget is not None:
                remaining = overall_budget.remaining
                if remaining <= 0:
                    _raise_combo_deadline()
                provider_timeout = (
                    remaining
                    if provider_timeout is None
                    else min(float(provider_timeout), remaining)
                )
                if provider_idle_timeout is not None and provider_idle_timeout > provider_timeout:
                    provider_idle_timeout = provider_timeout

            current_attempt_started = True
            try:
                result = ask_provider(
                    provider_id,
                    prompt,
                    role=step.role,
                    use_default_role=False,
                    model=step.model,
                    working_directory=working_directory,
                    timeout=provider_timeout,
                    idle_timeout=provider_idle_timeout,
                    skip_permissions=step.skip_permissions,
                    extra_args=step.extra_args,
                    heavy_model_authorized=heavy_model_authorized,
                    task_type=task_type,
                    service_profile=resolved_profile.id,
                    execution_id=execution_lease_id,
                    attempt_id=current_attempt_lease_id,
                    on_execution_update=_capture_execution_update,
                    execution_control=execution_control,
                )
            except Exception:
                # Never release merely because Python unwound.  A late
                # exception (for example telemetry after process exit) may
                # release only through the same authenticated terminal gate
                # used by normal paths; indeterminate failures remain held.
                _release_after_authenticated_terminal_snapshot()
                raise
            last_execution_meta = result.execution

            if (
                result.execution is not None
                and result.execution.get("state") == ExecutionState.CANCELLED.value
                and result.timed_out is False
            ):
                _lease_release(result.execution)
                result.duration_s = time.monotonic() - start_time
                return result

            # Sucesso ou falha verificável (ex.: output vazio com exit 0)
            if result.exit_code == 0 and not result.timed_out:
                if not (result.output or "").strip():
                    last_error = "output vazio"
                    _record_and_maybe_escalate(
                        result=result,
                        provider_id=provider_id,
                        last_error_value=last_error,
                    )
                    block_reason = _fallback_block_reason(
                        result,
                        require_execution_metadata=canonical_workspace is not None,
                        expected_execution_id=execution_lease_id if canonical_workspace else None,
                        expected_attempt_id=current_attempt_lease_id if canonical_workspace else None,
                    )
                    if not combo.failover_policy.on_error:
                        _lease_release(result.execution)
                        result.duration_s = time.monotonic() - start_time
                        return result
                    if block_reason is not None:
                        result.duration_s = time.monotonic() - start_time
                        _record_and_maybe_escalate(
                            result=result,
                            provider_id=provider_id,
                            last_error_value=block_reason,
                            unconfirmed_termination=True,
                        )
                        raise FallbackBlocked(
                            f"Fallback bloqueado após output vazio de '{provider_id}' em '{combo_id}': "
                            f"{block_reason}."
                        )
                    if not resolved_profile.allow_fallback_on_error:
                        _lease_release(result.execution)
                        result.duration_s = time.monotonic() - start_time
                        return result
                    break
                if verify:
                    if overall_budget is not None and overall_budget.expired:
                        _raise_combo_deadline()
                    from athena.reliability import record_verdict
                    from athena.verifier import verify_report

                    verifier_attempt_id = uuid.uuid4().hex
                    if canonical_workspace is not None:
                        workspace_lease.transfer(
                            canonical_workspace,
                            execution_lease_id,
                            current_attempt_lease_id or "",
                            verifier_attempt_id,
                            execution_meta=result.execution,
                        )
                    current_attempt_lease_id = verifier_attempt_id
                    current_attempt_started = False
                    verify_kwargs = dict(
                        working_directory=working_directory,
                        executor_provider=provider_id,
                        verification_timeout_s=(
                            min(verification_base_timeout, overall_budget.remaining)
                            if overall_budget is not None
                            else verification_base_timeout
                        ),
                        execution_id=execution_lease_id,
                        attempt_id=verifier_attempt_id,
                        on_execution_update=on_execution_update,
                        execution_control=execution_control,
                    )
                    if verify_kwargs["verification_timeout_s"] <= 0:
                        _raise_combo_deadline()
                    if overall_budget is not None and overall_budget.expired:
                        _raise_combo_deadline()
                    current_attempt_started = True
                    verify_kwargs["on_execution_update"] = _capture_execution_update
                    try:
                        verdict = verify_report(
                            prompt,
                            result.output,
                            **verify_kwargs,
                        )
                    except Exception:
                        _release_after_authenticated_terminal_snapshot()
                        raise
                    if canonical_workspace is not None and verdict.execution is None:
                        raise FallbackBlocked(
                            f"Fase de verificação sem metadados em '{combo_id}': "
                            "lease retida por fail-closed."
                        )
                    last_execution_meta = verdict.execution
                    record_verdict(
                        provider_id,
                        verdict,
                        task_excerpt=prompt,
                        project=working_directory or "",
                    )
                    result.verdict = verdict.to_dict()
                    result.execution = verdict.execution
                    if verdict.execution is not None:
                        safe_verifier_end, verifier_block_reason = _is_safe_terminal_metadata(
                            verdict.execution,
                            expected_execution_id=execution_lease_id if canonical_workspace else None,
                            expected_attempt_id=current_attempt_lease_id if canonical_workspace else None,
                        )
                        if not safe_verifier_end and canonical_workspace is not None:
                            raise FallbackBlocked(
                                f"Fase de verificação bloqueou continuação em '{combo_id}': "
                                f"{verifier_block_reason}."
                            )
                    if verdict.execution and verdict.execution.get("state") == ExecutionState.CANCELLED.value:
                        _lease_release(verdict.execution)
                        result.exit_code = 130
                        result.error = verdict.execution.get("termination_reason") or "verifier_cancelled"
                        result.duration_s = time.monotonic() - start_time
                        return result
                    if verdict.execution and verdict.execution.get("state") == ExecutionState.TERMINATION_UNCONFIRMED.value:
                        result.duration_s = time.monotonic() - start_time
                        _record_and_maybe_escalate(
                            result=result,
                            provider_id=provider_id,
                            last_error_value="terminação do verificador não confirmada",
                            unconfirmed_termination=True,
                        )
                        raise FallbackBlocked(
                            f"Fallback bloqueado após verificação de '{provider_id}' em '{combo_id}': "
                            "terminação do verificador não confirmada (TERMINATION_UNCONFIRMED)."
                        )
                    if verdict.verdadeiro is False:
                        # Alegação marcada FALSE conta como falha para a política.
                        last_error = (
                            f"Relatório FALSO ({verdict.verificador}): "
                            f"{'; '.join(verdict.motivos[:2]) or 'inconsistente'}"
                        )
                        result.warnings.append(
                            f"Relatório de '{provider_id}' marcado FALSO pelo verificador "
                            f"{verdict.verificador}; tentando próximo provider do combo."
                        )
                        _record_and_maybe_escalate(
                            result=result,
                            provider_id=provider_id,
                            last_error_value=last_error,
                            false_verdict=True,
                        )
                        break  # Vai para próximo provider
                    if verdict.verdadeiro is None:
                        result.warnings.append(
                            "Verificação indisponível — relatório aceito sem checagem."
                        )
                # Sucesso real (exit_code=0 e sem timeout): tente liberar o
                # lease usando a política de segurança. Se os metadados não
                # confirmarem terminação segura, release_after_attempt()
                # mantém o lease retido (fail-closed) mesmo no caminho de
                # sucesso.
                _annotate_reinforced_verification(
                    result, step=step, step_index=step_index
                )
                _lease_release(last_execution_meta)
                result.duration_s = time.monotonic() - start_time
                return result

            # Timeout — verifica se deve fazer failover
            if result.timed_out:
                timeout_value = provider_timeout if provider_timeout is not None else "?"
                last_error = f"Timeout ({timeout_value}s)"
                block_reason = _fallback_block_reason(
                    result,
                    require_execution_metadata=canonical_workspace is not None,
                    expected_execution_id=execution_lease_id if canonical_workspace else None,
                    expected_attempt_id=current_attempt_lease_id if canonical_workspace else None,
                )
                if not combo.failover_policy.on_timeout:
                    # Não faz failover em timeout — retorna o erro
                    _lease_release(result.execution)
                    result.duration_s = time.monotonic() - start_time
                    return result
                if block_reason is not None:
                    result.duration_s = time.monotonic() - start_time
                    _record_and_maybe_escalate(
                        result=result,
                        provider_id=provider_id,
                        last_error_value=block_reason,
                        unconfirmed_termination=True,
                    )
                    raise FallbackBlocked(
                        f"Fallback bloqueado após timeout de '{provider_id}' em '{combo_id}': "
                        f"{block_reason}."
                    )
                if not resolved_profile.allow_fallback_on_timeout:
                    _lease_release(result.execution)
                    result.duration_s = time.monotonic() - start_time
                    return result
                _record_and_maybe_escalate(
                    result=result,
                    provider_id=provider_id,
                    last_error_value=last_error,
                )
                break  # Vai para próximo provider

            # Erro de execução (exit_code != 0)
            if result.exit_code != 0:
                last_error = result.error or f"Exit code {result.exit_code}"
                block_reason = _fallback_block_reason(
                    result,
                    require_execution_metadata=canonical_workspace is not None,
                    expected_execution_id=execution_lease_id if canonical_workspace else None,
                    expected_attempt_id=current_attempt_lease_id if canonical_workspace else None,
                )
                if not combo.failover_policy.on_error:
                    _lease_release(result.execution)
                    result.duration_s = time.monotonic() - start_time
                    return result
                if block_reason is not None:
                    result.duration_s = time.monotonic() - start_time
                    _record_and_maybe_escalate(
                        result=result,
                        provider_id=provider_id,
                        last_error_value=block_reason,
                        unconfirmed_termination=True,
                    )
                    raise FallbackBlocked(
                        f"Fallback bloqueado após falha de '{provider_id}' em '{combo_id}': "
                        f"{block_reason}."
                    )
                if not resolved_profile.allow_fallback_on_error:
                    _lease_release(result.execution)
                    result.duration_s = time.monotonic() - start_time
                    return result
                _record_and_maybe_escalate(
                    result=result,
                    provider_id=provider_id,
                    last_error_value=last_error,
                )
                break  # Vai para próximo provider

            # Retry delay (se não for última tentativa)
            if attempt < combo.failover_policy.max_retries_per_provider - 1:
                if overall_budget is not None:
                    if overall_budget.expired:
                        _raise_combo_deadline()
                    delay_s = min(
                        combo.failover_policy.retry_delay_seconds,
                        overall_budget.remaining,
                    )
                    if delay_s > 0:
                        time.sleep(delay_s)
                else:
                    time.sleep(combo.failover_policy.retry_delay_seconds)

    # Todos os providers falharam sem que nenhum bloqueio de segurança tenha
    # sido levantado -- por construção, todo `break` acima só é alcançado
    # quando o lease já foi liberado (ou nunca existiu), então isto é
    # idempotente, não um novo caminho de liberação.
    _lease_release(last_execution_meta)
    error_msg = (
        f"Combo '{combo_id}' esgotou todos os {len(active_chain)} providers. "
        f"Tentados: {', '.join(attempted)}. "
        f"Último erro: {last_error or 'desconhecido'} "
        f"(duração total: {time.monotonic() - start_time:.1f}s)."
    )
    raise AllProvidersFailed(error_msg)


def test_combo(combo_id: str) -> list[dict]:
    """Testa um combo pingando cada provider com um prompt mínimo.

    Retorna lista de resultados por provider (sem lançar exceção).
    """
    combo = get_combo(combo_id)
    if combo is None:
        return [{"provider_id": None, "status": "not_found", "error": f"Combo '{combo_id}' não encontrado"}]

    results = []
    test_prompt = "Responda apenas com a palavra 'OK'."

    for step in combo.chain:
        try:
            spec = PROVIDERS.get(step.provider_id)
            if step.timeout is not None:
                test_timeout = step.timeout
            elif spec is not None:
                test_timeout = max(90, min(spec.default_timeout, 120))
            else:
                test_timeout = 120

            result = ask_provider(
                step.provider_id,
                test_prompt,
                role=None,
                use_default_role=False,
                model=step.model,
                timeout=test_timeout,
                skip_permissions=True,
            )
            results.append({
                "provider_id": step.provider_id,
                "status": "ok" if result.exit_code == 0 else "error",
                "exit_code": result.exit_code,
                "duration_s": round(result.duration_s, 2),
                "error": result.error,
            })
        except Exception as exc:
            results.append({
                "provider_id": step.provider_id,
                "status": "exception",
                "error": str(exc),
            })

    return results
