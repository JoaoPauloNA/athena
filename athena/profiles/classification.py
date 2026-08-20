"""Classificação conservadora de perfis de serviço."""

from __future__ import annotations

from .contracts import ServiceProfile


def classify_service_profile(value: object | None) -> ServiceProfile:
    """Classificar um identificador, usando ``unknown`` como default seguro."""
    if isinstance(value, ServiceProfile):
        return value
    if not isinstance(value, str):
        return ServiceProfile.UNKNOWN
    try:
        return ServiceProfile(value)
    except ValueError:
        return ServiceProfile.UNKNOWN


def resolve_service_profile(
    *,
    explicit_profile_id: object | None = None,
    provider_id: object | None = None,
    task_type: object | None = None,
    working_directory: object | None = None,
) -> ServiceProfile:
    """Resolver sinais conhecidos sem assumir um perfil quando eles faltam."""
    if explicit_profile_id is not None:
        return classify_service_profile(explicit_profile_id)
    if provider_id == "ollama":
        return ServiceProfile.LOCAL_MODEL
    if task_type in {"frontend", "backend"}:
        return ServiceProfile.CODE_AGENT
    if task_type == "raciocinio":
        return ServiceProfile.RESEARCH
    if task_type == "rapidez":
        return ServiceProfile.TEXT_GENERATION
    if isinstance(working_directory, str) and working_directory:
        return ServiceProfile.CODE_AGENT
    return ServiceProfile.UNKNOWN
