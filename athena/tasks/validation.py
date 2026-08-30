"""Validação e canonicalização da submissão de tarefa, sem I/O."""

from __future__ import annotations

import json
import math
import re
import unicodedata
from typing import Any

from .contracts import (
    DEFAULT_PRIORITY,
    MAX_CANONICAL_TASK_BYTES,
    MAX_IDEMPOTENCY_KEY_BYTES,
    MAX_INPUT_BYTES,
    MAX_JSON_DEPTH,
    MAX_JSON_ITEMS,
    MAX_PRIORITY,
    MAX_PROJECT_REF_BYTES,
    MAX_TASK_TYPE_LENGTH,
    MIN_PRIORITY,
    TaskSubmission,
    TaskValidationError,
)

_TASK_TYPE_RE = re.compile(r"[a-z][a-z0-9_.-]*")
_TOP_LEVEL_KEYS = frozenset(
    {"task_type", "input", "project_ref", "constraints", "expected_output", "priority"}
)
_BOUNDED_OBJECT_KEYS = ("constraints", "expected_output")

_NORMALIZED_SECRET_KEYS = {
    "apikey",
    "authorization",
    "bearertoken",
    "clientsecret",
    "password",
    "secret",
    "token",
}


def _normalized_key(key: str) -> str:
    normalized = unicodedata.normalize("NFKC", key).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _is_secret_key(key: str) -> bool:
    normalized = _normalized_key(key)
    return (
        normalized in _NORMALIZED_SECRET_KEYS
        or "secret" in normalized
        or "password" in normalized
        or normalized.endswith(("apikey", "token", "authorization"))
    )


def _invalid(code: str, message: str) -> TaskValidationError:
    return TaskValidationError(code, message)


def _check_bounds(value: Any, *, depth: int = 1) -> int:
    """Rejeitar profundidade/itens excessivos, chaves de segredo e não-finitos."""
    if depth > MAX_JSON_DEPTH:
        raise _invalid("INVALID_TASK", "task excede profundidade máxima")
    if isinstance(value, bool):
        return 1
    if isinstance(value, float) and not math.isfinite(value):
        raise _invalid("INVALID_TASK", "task contém número não finito")
    count = 1
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise _invalid("INVALID_TASK", "task contém chave não textual")
            if _is_secret_key(key):
                raise _invalid("INVALID_TASK", "task contém campo de segredo proibido")
            count += _check_bounds(child, depth=depth + 1)
            if count > MAX_JSON_ITEMS:
                raise _invalid("TASK_TOO_LARGE", "task excede contagem máxima de itens")
    elif isinstance(value, list):
        for child in value:
            count += _check_bounds(child, depth=depth + 1)
            if count > MAX_JSON_ITEMS:
                raise _invalid("TASK_TOO_LARGE", "task excede contagem máxima de itens")
    return count


def _utf8_len(value: str) -> int:
    return len(value.encode("utf-8", errors="strict"))


def validate_idempotency_key(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise _invalid("INVALID_TASK", "idempotency_key deve ser string não vazia")
    if _utf8_len(value) > MAX_IDEMPOTENCY_KEY_BYTES:
        raise _invalid("TASK_TOO_LARGE", "idempotency_key excede tamanho máximo")
    return value


def _validate_task_type(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_TASK_TYPE_LENGTH
        or not _TASK_TYPE_RE.fullmatch(value)
    ):
        raise _invalid("INVALID_TASK", "task_type deve ser identificador canônico")
    return value


def _validate_input(value: Any) -> str:
    if not isinstance(value, str):
        raise _invalid("INVALID_TASK", "input deve ser string")
    if _utf8_len(value) > MAX_INPUT_BYTES:
        raise _invalid("TASK_TOO_LARGE", "input excede tamanho máximo")
    return value


def _validate_project_ref(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise _invalid("INVALID_TASK", "project_ref deve ser string não vazia")
    if _utf8_len(value) > MAX_PROJECT_REF_BYTES:
        raise _invalid("TASK_TOO_LARGE", "project_ref excede tamanho máximo")
    return value


def _validate_bounded_object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _invalid("INVALID_TASK", f"{name} deve ser objeto")
    _check_bounds(value)
    return value


def _validate_priority(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _invalid("INVALID_TASK", "priority deve ser inteiro")
    if value < MIN_PRIORITY or value > MAX_PRIORITY:
        raise _invalid("INVALID_TASK", "priority fora do intervalo permitido")
    return value


def build_submission(idempotency_key: Any, task: Any) -> TaskSubmission:
    """Validar entrada bruta e produzir uma submissão canonicalizada estável."""
    key = validate_idempotency_key(idempotency_key)

    if not isinstance(task, dict):
        raise _invalid("INVALID_TASK", "task deve ser objeto")
    unknown = set(task) - _TOP_LEVEL_KEYS
    if unknown:
        raise _invalid("INVALID_TASK", "task contém campo não suportado")

    if "task_type" not in task:
        raise _invalid("INVALID_TASK", "task_type é obrigatório")
    if "input" not in task:
        raise _invalid("INVALID_TASK", "input é obrigatório")

    task_type = _validate_task_type(task["task_type"])
    task_input = _validate_input(task["input"])

    canonical: dict[str, Any] = {"task_type": task_type, "input": task_input}

    if "project_ref" in task:
        canonical["project_ref"] = _validate_project_ref(task["project_ref"])

    for name in _BOUNDED_OBJECT_KEYS:
        if name in task:
            canonical[name] = _validate_bounded_object(task[name], name)

    priority = _validate_priority(task.get("priority", DEFAULT_PRIORITY))
    canonical["priority"] = priority

    canonical_json = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    if _utf8_len(canonical_json) > MAX_CANONICAL_TASK_BYTES:
        raise _invalid("TASK_TOO_LARGE", "task canônica excede tamanho máximo")

    return TaskSubmission(
        idempotency_key=key,
        task_type=task_type,
        canonical_json=canonical_json,
        priority=priority,
    )
