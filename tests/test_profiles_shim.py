"""Testes da identidade dos contratos expostos pelo shim de perfis."""

from aegis.contracts import (
    FailureCondition as AegisFailureCondition,
)
from aegis.contracts import (
    FallbackPolicy as AegisFallbackPolicy,
)
from aegis.contracts import (
    ServiceProfile as AegisServiceProfile,
)

from athena.profiles import FailureCondition, FallbackPolicy, ServiceProfile


def test_profile_contracts_are_the_exact_aegis_types() -> None:
    assert ServiceProfile is AegisServiceProfile
    assert FailureCondition is AegisFailureCondition
    assert FallbackPolicy is AegisFallbackPolicy
