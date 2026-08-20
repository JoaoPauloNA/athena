"""Equivalência exaustiva entre a fachada de decisão e a política de fallback."""

from aegis.contracts import FailureCondition, RiskContext, ServiceProfile
from aegis.decision import evaluate
from aegis.policy import allows_automatic_fallback


def test_decision_matches_fallback_policy_for_every_enum_combination() -> None:
    for profile in ServiceProfile:
        for condition in FailureCondition:
            decision = evaluate(
                RiskContext(
                    requested_action="automatic_fallback",
                    explicit_profile_id=profile,
                    failure_condition=condition,
                )
            )

            assert decision.approved == allows_automatic_fallback(profile, condition)
