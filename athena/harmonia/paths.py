"""Canonicalização de caminhos e detecção de escopo inválido."""

from __future__ import annotations

import os
from pathlib import Path

from .contracts import REASON_PATH_INVALID, HarmoniaError


def canonicalize_path(path: str, *, workspace_root: str) -> Path:
    if not isinstance(path, str) or not path:
        raise HarmoniaError(REASON_PATH_INVALID)
    if os.path.isabs(path):
        candidate = Path(path)
    else:
        candidate = Path(workspace_root) / path
    normalized = os.path.normpath(candidate)
    if ".." in Path(normalized).parts:
        raise HarmoniaError(REASON_PATH_INVALID)
    try:
        resolved = Path(os.path.realpath(normalized))
    except OSError as exc:
        raise HarmoniaError(REASON_PATH_INVALID) from exc
    root = Path(os.path.realpath(workspace_root))
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise HarmoniaError(REASON_PATH_INVALID) from exc
    return resolved


def canonicalize_scope(
    paths: tuple[str, ...],
    *,
    workspace_root: str,
) -> tuple[Path, ...]:
    canonical: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = canonicalize_path(path, workspace_root=workspace_root)
        if resolved in seen:
            continue
        seen.add(resolved)
        canonical.append(resolved)
    return tuple(sorted(canonical))
