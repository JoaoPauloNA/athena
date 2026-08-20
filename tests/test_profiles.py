"""Testes dos perfis e de sua política conservadora de fallback."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from athena.profiles import (
    FALLBACK_POLICIES,
    FailureCondition,
    ServiceProfile,
    allows_automatic_fallback,
    classify_service_profile,
    resolve_service_profile,
)

EXPECTED_PROFILES = {
    "text_generation",
    "code_agent",
    "build_test",
    "research",
    "local_model",
    "verification",
    "workspace_mutation",
    "authenticated_external",
    "unknown",
}


def test_service_profiles_are_exactly_the_contract_set() -> None:
    assert {profile.value for profile in ServiceProfile} == EXPECTED_PROFILES
    assert set(FALLBACK_POLICIES) == set(ServiceProfile)


@pytest.mark.parametrize(
    ("profile", "condition", "expected"),
    [
        (ServiceProfile.TEXT_GENERATION, FailureCondition.TIMEOUT, True),
        (ServiceProfile.CODE_AGENT, FailureCondition.NETWORK_ERROR, True),
        (ServiceProfile.BUILD_TEST, FailureCondition.PROVIDER_ERROR, True),
        (ServiceProfile.RESEARCH, FailureCondition.OTHER, True),
        (ServiceProfile.LOCAL_MODEL, FailureCondition.TIMEOUT, True),
        (ServiceProfile.VERIFICATION, FailureCondition.PROVIDER_ERROR, True),
        (ServiceProfile.WORKSPACE_MUTATION, FailureCondition.NETWORK_ERROR, True),
        (ServiceProfile.WORKSPACE_MUTATION, FailureCondition.TIMEOUT, False),
        (ServiceProfile.TEXT_GENERATION, FailureCondition.CANCELLATION, False),
    ],
)
def test_automatic_fallback_policy_by_profile(
    profile: ServiceProfile,
    condition: FailureCondition,
    expected: bool,
) -> None:
    assert allows_automatic_fallback(profile, condition) is expected


@pytest.mark.parametrize(
    "profile",
    [ServiceProfile.AUTHENTICATED_EXTERNAL, ServiceProfile.UNKNOWN],
)
@pytest.mark.parametrize("condition", list(FailureCondition))
def test_sensitive_and_unknown_profiles_never_allow_automatic_fallback(
    profile: ServiceProfile,
    condition: FailureCondition,
) -> None:
    assert allows_automatic_fallback(profile, condition) is False


@pytest.mark.parametrize("profile", ["authenticated_external", "unknown"])
def test_sensitive_and_unknown_profiles_reject_unmodelled_failure(profile: str) -> None:
    assert allows_automatic_fallback(profile, "future_failure") is False


@pytest.mark.parametrize("value", [None, "", "missing", object(), 42])
def test_unknown_or_absent_classification_is_fail_closed(value: object) -> None:
    assert classify_service_profile(value) is ServiceProfile.UNKNOWN
    assert allows_automatic_fallback(value, FailureCondition.PROVIDER_ERROR) is False


def test_classifier_accepts_only_known_profile_identifiers() -> None:
    for profile in ServiceProfile:
        assert classify_service_profile(profile.value) is profile
        assert classify_service_profile(profile) is profile


def test_resolver_preserves_known_classification_signals() -> None:
    assert resolve_service_profile(provider_id="ollama") is ServiceProfile.LOCAL_MODEL
    assert resolve_service_profile(task_type="backend") is ServiceProfile.CODE_AGENT
    assert resolve_service_profile(task_type="raciocinio") is ServiceProfile.RESEARCH
    assert resolve_service_profile(task_type="rapidez") is ServiceProfile.TEXT_GENERATION
    assert resolve_service_profile(working_directory="/workspace") is ServiceProfile.CODE_AGENT
    assert resolve_service_profile() is ServiceProfile.UNKNOWN


def test_profiles_package_does_not_import_other_athena_packages() -> None:
    profiles_directory = Path(__file__).resolve().parents[1] / "athena" / "profiles"
    forbidden_imports: list[str] = []

    for module_path in profiles_directory.glob("*.py"):
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                forbidden_imports.extend(
                    alias.name for alias in node.names if alias.name.startswith("athena.")
                )
            elif (
                isinstance(node, ast.ImportFrom)
                and node.level == 0
                and node.module is not None
                and node.module.startswith("athena.")
            ):
                forbidden_imports.append(node.module)

    assert forbidden_imports == []
