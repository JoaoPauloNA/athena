from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ServiceProfile:
    id: str
    default_absolute_timeout_s: float | None
    max_absolute_timeout_s: float
    default_idle_timeout_s: float | None
    allow_fallback_on_error: bool
    allow_fallback_on_timeout: bool
    requires_workspace: bool
    authenticated_external: bool
    rationale: str


SERVICE_PROFILES: dict[str, ServiceProfile] = {
    "text_generation": ServiceProfile(
        id="text_generation",
        default_absolute_timeout_s=None,
        max_absolute_timeout_s=600.0,
        default_idle_timeout_s=None,
        allow_fallback_on_error=True,
        allow_fallback_on_timeout=True,
        requires_workspace=False,
        authenticated_external=False,
        rationale="Texto curto e direto com teto conservador.",
    ),
    "code_agent": ServiceProfile(
        id="code_agent",
        default_absolute_timeout_s=None,
        max_absolute_timeout_s=1800.0,
        default_idle_timeout_s=None,
        allow_fallback_on_error=True,
        allow_fallback_on_timeout=True,
        requires_workspace=True,
        authenticated_external=False,
        rationale="Edição de código tende a ser mais longa e local.",
    ),
    "build_test": ServiceProfile(
        id="build_test",
        default_absolute_timeout_s=None,
        max_absolute_timeout_s=1200.0,
        default_idle_timeout_s=None,
        allow_fallback_on_error=True,
        allow_fallback_on_timeout=True,
        requires_workspace=True,
        authenticated_external=False,
        rationale="Build/test exige workspace e janela de execução moderada.",
    ),
    "research": ServiceProfile(
        id="research",
        default_absolute_timeout_s=None,
        max_absolute_timeout_s=1800.0,
        default_idle_timeout_s=None,
        allow_fallback_on_error=True,
        allow_fallback_on_timeout=True,
        requires_workspace=False,
        authenticated_external=False,
        rationale="Pesquisa/raciocínio pode levar mais tempo.",
    ),
    "local_model": ServiceProfile(
        id="local_model",
        default_absolute_timeout_s=None,
        max_absolute_timeout_s=900.0,
        default_idle_timeout_s=None,
        allow_fallback_on_error=True,
        allow_fallback_on_timeout=True,
        requires_workspace=False,
        authenticated_external=False,
        rationale="Modelo local com teto intermediário conservador.",
    ),
    "verification": ServiceProfile(
        id="verification",
        default_absolute_timeout_s=None,
        max_absolute_timeout_s=600.0,
        default_idle_timeout_s=None,
        allow_fallback_on_error=True,
        allow_fallback_on_timeout=True,
        requires_workspace=True,
        authenticated_external=False,
        rationale="Verificação deve ser rápida e evidenciável.",
    ),
    "workspace_mutation": ServiceProfile(
        id="workspace_mutation",
        default_absolute_timeout_s=None,
        max_absolute_timeout_s=1800.0,
        default_idle_timeout_s=None,
        allow_fallback_on_error=True,
        allow_fallback_on_timeout=False,
        requires_workspace=True,
        authenticated_external=False,
        rationale="Mutação de workspace admite fallback em erro, não em timeout.",
    ),
    "authenticated_external": ServiceProfile(
        id="authenticated_external",
        default_absolute_timeout_s=None,
        max_absolute_timeout_s=900.0,
        default_idle_timeout_s=None,
        allow_fallback_on_error=False,
        allow_fallback_on_timeout=False,
        requires_workspace=False,
        authenticated_external=True,
        rationale="Ação autenticada externa evita repetição automática.",
    ),
    "unknown": ServiceProfile(
        id="unknown",
        default_absolute_timeout_s=None,
        max_absolute_timeout_s=300.0,
        default_idle_timeout_s=None,
        allow_fallback_on_error=False,
        allow_fallback_on_timeout=False,
        requires_workspace=False,
        authenticated_external=False,
        rationale="Contexto não classificável com política fail-closed.",
    ),
}

SERVICE_PROFILE_IDS: tuple[str, ...] = tuple(SERVICE_PROFILES.keys())


def get_service_profile(profile_id: str) -> ServiceProfile:
    try:
        return SERVICE_PROFILES[profile_id]
    except KeyError as exc:
        raise ValueError(f"service_profile inválido: {profile_id}") from exc


def resolve_service_profile(
    *,
    explicit_profile_id: str | None,
    provider_id: str,
    task_type: str | None,
    working_directory: str | None,
) -> ServiceProfile:
    if explicit_profile_id is not None:
        return get_service_profile(explicit_profile_id)
    if provider_id == "ollama":
        return SERVICE_PROFILES["local_model"]
    if task_type in {"frontend", "backend"}:
        return SERVICE_PROFILES["code_agent"]
    if task_type == "raciocinio":
        return SERVICE_PROFILES["research"]
    if task_type == "rapidez":
        return SERVICE_PROFILES["text_generation"]
    if working_directory:
        return SERVICE_PROFILES["code_agent"]
    return SERVICE_PROFILES["text_generation"]


def _validate_positive_finite(name: str, value: float | int) -> float:
    if isinstance(value, bool) or float(value) <= 0 or not math.isfinite(float(value)):
        raise ValueError(f"{name} deve ser número positivo finito")
    return float(value)


def resolve_timeouts(
    *,
    profile: ServiceProfile,
    provider_default_absolute_timeout_s: float | int | None,
    explicit_absolute_timeout_s: float | int | None,
    explicit_idle_timeout_s: float | int | None,
) -> tuple[float | None, float | None]:
    if explicit_absolute_timeout_s is not None:
        absolute_timeout_s = _validate_positive_finite(
            "timeout", explicit_absolute_timeout_s
        )
        if absolute_timeout_s > profile.max_absolute_timeout_s:
            raise ValueError(
                f"timeout ({absolute_timeout_s}) excede max do profile '{profile.id}' "
                f"({profile.max_absolute_timeout_s})"
            )
    else:
        if provider_default_absolute_timeout_s is None:
            absolute_timeout_s = profile.default_absolute_timeout_s
        else:
            absolute_timeout_s = _validate_positive_finite(
                "provider_default_timeout",
                provider_default_absolute_timeout_s,
            )
        if absolute_timeout_s is not None:
            absolute_timeout_s = min(absolute_timeout_s, profile.max_absolute_timeout_s)

    if explicit_idle_timeout_s is None:
        idle_timeout_s = profile.default_idle_timeout_s
    else:
        idle_timeout_s = _validate_positive_finite("idle_timeout", explicit_idle_timeout_s)
        if absolute_timeout_s is None:
            raise ValueError("idle_timeout exige timeout absoluto efetivo")
        if idle_timeout_s > absolute_timeout_s:
            raise ValueError("idle_timeout deve ser <= timeout absoluto efetivo")

    return absolute_timeout_s, idle_timeout_s
