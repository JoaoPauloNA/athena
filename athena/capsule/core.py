"""Construção e verificação da Cápsula contra contratos públicos Aegis."""

from __future__ import annotations

from dataclasses import asdict

from aegis import (
    CONTRACT_VERSION,
    EXECUTE_LOCAL_CLI,
    POLICY_VERSION,
    SCHEMA_VERSION,
    ExecutionAuthorizationRequest,
    canonical_digest,
    issue_execution_seal,
    verify_execution_seal,
)

from .contracts import CapsuleDenied, ExecutionCapsule, ExecutionPlan


def plan_digest(plan: ExecutionPlan) -> str:
    """Calcular o digest determinístico do snapshot completo."""
    return canonical_digest(asdict(plan))


def _authorization_request(plan: ExecutionPlan) -> ExecutionAuthorizationRequest:
    digest = plan_digest(plan)
    if plan.absolute_timeout_s is None:
        raise CapsuleDenied("CAPSULE_INVALID")
    return ExecutionAuthorizationRequest(
        schema_version=SCHEMA_VERSION,
        contract_version=CONTRACT_VERSION,
        policy_version=POLICY_VERSION,
        requested_action=EXECUTE_LOCAL_CLI,
        plan_digest=digest,
        task_id=plan.task_id,
        execution_id=plan.execution_id,
        attempt_id=plan.attempt_id,
        provider_id=plan.provider_id,
        access_mode=plan.access_mode,
        command_digest=canonical_digest(plan.command),
        cwd_digest=canonical_digest(plan.cwd),
        allowed_environment_names=plan.environment_names,
        environment_values_digest=plan.environment_values_digest,
        network_policy=plan.network_policy,
        resource_scope=plan.resource_scope,
        write_scope=plan.write_scope,
        time_budget_s=plan.absolute_timeout_s,
        fallback_declared=plan.fallback_declared,
        tests=plan.tests,
        log_level=plan.log_level,
    )


def issue_capsule(
    plan: ExecutionPlan,
    key: bytes,
    *,
    now: int | None = None,
) -> ExecutionCapsule:
    """Solicitar um selo Aegis interno para o plano exato."""
    request = _authorization_request(plan)
    decision = issue_execution_seal(request, key, now=now)
    if not decision.approved or decision.seal is None:
        raise CapsuleDenied(decision.reason_code.value)
    return ExecutionCapsule(plan, request.plan_digest, decision.seal)


def verify_capsule(
    capsule: object,
    expected_plan: ExecutionPlan,
    key: bytes,
    *,
    now: int | None = None,
) -> None:
    """Negar qualquer drift antes de permitir a fronteira Iris."""
    if not isinstance(capsule, ExecutionCapsule):
        raise CapsuleDenied("CAPSULE_MISSING")
    expected_digest = plan_digest(expected_plan)
    if capsule.plan_digest != expected_digest or capsule.plan != expected_plan:
        raise CapsuleDenied("CAPSULE_INVALID")
    request = _authorization_request(expected_plan)
    decision = verify_execution_seal(capsule.seal, request, key, now=now)
    if not decision.approved:
        raise CapsuleDenied(decision.reason_code.value)
