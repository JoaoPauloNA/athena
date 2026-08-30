"""Verificação de alterações reais no filesystem — não confia no executor."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

MAX_SNAPSHOT_PATHS = 256
MAX_SNAPSHOT_DEPTH = 4
MAX_SNAPSHOT_BYTES = 65_536
MAX_HASH_BYTES = 65_536


@dataclass(frozen=True, slots=True)
class PathFingerprint:
    mtime_ns: int
    size: int
    ino: int
    content_hash: str | None


@dataclass(frozen=True, slots=True)
class InventoryResult:
    snapshots: dict[str, PathFingerprint | None]
    complete: bool
    exceeded: bool


def _hash_file_bounded(path: Path) -> str | None:
    try:
        size = path.stat().st_size
        if size > MAX_HASH_BYTES:
            return None
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(8192)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _stat_fingerprint(path: Path) -> PathFingerprint | None:
    try:
        stat = path.stat()
        content_hash = None
        if path.is_file():
            content_hash = _hash_file_bounded(path)
        return PathFingerprint(stat.st_mtime_ns, stat.st_size, stat.st_ino, content_hash)
    except OSError:
        return None


def _capture_path(
    root: Path,
    current: Path,
    *,
    depth: int,
    max_depth: int,
    captured: dict[str, PathFingerprint | None],
    count: list[int],
    total_bytes: list[int],
) -> bool:
    """Return False when inventory bounds exceeded."""
    if count[0] >= MAX_SNAPSHOT_PATHS:
        return False
    key = str(current.relative_to(root))
    if key not in captured:
        fp = _stat_fingerprint(current)
        captured[key] = fp
        count[0] += 1
        if fp is not None and current.is_file():
            total_bytes[0] += fp.size
            if total_bytes[0] > MAX_SNAPSHOT_BYTES:
                return False
    if current.is_dir() and depth < max_depth:
        try:
            children = sorted(current.iterdir())
        except OSError:
            return True
        for child in children:
            if not _capture_path(
                root,
                child,
                depth=depth + 1,
                max_depth=max_depth,
                captured=captured,
                count=count,
                total_bytes=total_bytes,
            ):
                return False
    return True


def inventory_workspace(
    workspace_root: str,
    *,
    relative_paths: tuple[str, ...] = (),
    max_depth: int = MAX_SNAPSHOT_DEPTH,
    full_workspace: bool = False,
) -> InventoryResult:
    """Inventário limitado com hashes de conteúdo; falha fechado se exceder limites."""
    root = Path(os.path.realpath(workspace_root))
    captured: dict[str, PathFingerprint | None] = {}
    count = [0]
    total_bytes = [0]
    complete = True
    exceeded = False

    if full_workspace or not relative_paths:
        try:
            entries = sorted(root.iterdir())
        except OSError:
            entries = []
        for entry in entries:
            if not _capture_path(
                root,
                entry,
                depth=0,
                max_depth=max_depth,
                captured=captured,
                count=count,
                total_bytes=total_bytes,
            ):
                complete = False
                exceeded = True
                break
    else:
        parents: set[Path] = set()
        for relative in relative_paths:
            target = root / relative
            resolved = Path(os.path.realpath(target))
            try:
                resolved.relative_to(root)
            except ValueError:
                continue
            parents.add(resolved.parent)
            if not _capture_path(
                root,
                resolved,
                depth=0,
                max_depth=max_depth,
                captured=captured,
                count=count,
                total_bytes=total_bytes,
            ):
                complete = False
                exceeded = True
                break
        if complete:
            for parent in sorted(parents):
                if count[0] >= MAX_SNAPSHOT_PATHS:
                    complete = False
                    exceeded = True
                    break
                try:
                    entries = sorted(parent.iterdir())
                except OSError:
                    continue
                for entry in entries:
                    key = str(entry.relative_to(root))
                    if key in captured:
                        continue
                    if not _capture_path(
                        root,
                        entry,
                        depth=0,
                        max_depth=0,
                        captured=captured,
                        count=count,
                        total_bytes=total_bytes,
                    ):
                        complete = False
                        exceeded = True
                        break
                if exceeded:
                    break

    return InventoryResult(snapshots=captured, complete=complete, exceeded=exceeded)


def snapshot_paths(
    workspace_root: str,
    *,
    relative_paths: tuple[str, ...],
    max_depth: int = MAX_SNAPSHOT_DEPTH,
    full_workspace: bool = False,
) -> dict[str, PathFingerprint | None]:
    """Capturar metadados e hashes limitados para caminhos autorizados e descendentes."""
    result = inventory_workspace(
        workspace_root,
        relative_paths=relative_paths,
        max_depth=max_depth,
        full_workspace=full_workspace,
    )
    return result.snapshots


def diff_snapshots(
    before: dict[str, PathFingerprint | None],
    after: dict[str, PathFingerprint | None],
) -> tuple[str, ...]:
    """Retornar caminhos relativos cujo metadado/hash mudou ou foi criado/removido."""
    keys = sorted(set(before) | set(after))
    changed: list[str] = []
    for key in keys:
        if before.get(key) != after.get(key):
            changed.append(key)
    return tuple(changed)


def is_infrastructure_path(relative_path: str) -> bool:
    """Caminhos gerenciados pelo runtime Harmonia — não são writes de subtask."""
    normalized = relative_path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = normalized.lstrip("/")
    if normalized in {".git", ".harmonia_worktrees"}:
        return True
    return normalized.startswith((".git/", ".harmonia_worktrees/"))


def filter_infrastructure_changes(changed: tuple[str, ...]) -> tuple[str, ...]:
    """Remover artefatos de worktree/git paralelos antes de verificar o repo principal."""
    return tuple(path for path in changed if not is_infrastructure_path(path))


def compute_evidence_digest(
    *,
    workspace_root: str,
    relative_paths: tuple[str, ...],
) -> tuple[str, tuple[str, ...]]:
    """Digest SHA256 sanitizado de alterações limitadas antes da limpeza."""
    snapshots = snapshot_paths(workspace_root, relative_paths=relative_paths)
    digest = hashlib.sha256()
    sanitized: list[str] = []
    for path in sorted(relative_paths):
        if len(sanitized) >= MAX_SNAPSHOT_PATHS:
            break
        normalized = path.replace("\\", "/").lstrip("./")
        sanitized.append(normalized)
        fp = snapshots.get(normalized)
        digest.update(normalized.encode("utf-8"))
        if fp is not None:
            digest.update(str(fp.mtime_ns).encode("utf-8"))
            digest.update(str(fp.size).encode("utf-8"))
            if fp.content_hash:
                digest.update(fp.content_hash.encode("utf-8"))
    return digest.hexdigest(), tuple(sanitized)


def map_worktree_changes(
    *,
    workspace_root: str,
    worktree_root: str,
    authorized_relative: tuple[str, ...],
    observed_relative: tuple[str, ...],
) -> tuple[str, ...]:
    """Mapear alterações da worktree para caminhos relativos autorizados originais."""
    repo = Path(os.path.realpath(workspace_root))
    wt = Path(os.path.realpath(worktree_root))
    allowed: set[str] = set()
    for relative in authorized_relative:
        canonical = str((repo / relative).resolve().relative_to(repo))
        allowed.add(canonical)
        allowed.add(relative.replace("\\", "/"))
    mapped: list[str] = []
    for observed in observed_relative:
        normalized = observed.replace("\\", "/").lstrip("./")
        if normalized in allowed:
            mapped.append(normalized)
            continue
        candidate = str((wt / normalized).resolve().relative_to(repo))
        if candidate in allowed:
            mapped.append(candidate)
    return tuple(sorted(set(mapped)))
