"""Autoridade de worktree — produção falha fechado; testes usam Git sintético."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from .contracts import (
    REASON_WORKTREE_CLEANUP_FAILED,
    HarmoniaError,
    WorktreeAuthority,
    WorktreeDeniedError,
)

_OPAQUE_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


class DenyWorktreeAuthority:
    """Implementação padrão: worktree sempre negada."""

    def create_worktree(
        self,
        *,
        repository_root: str,
        base_ref: str,
        opaque_name: str,
    ) -> str:
        raise WorktreeDeniedError()

    def remove_worktree(self, worktree_path: str) -> None:
        raise WorktreeDeniedError()


def _validate_opaque_name(opaque_name: str) -> str:
    if not isinstance(opaque_name, str):
        raise WorktreeDeniedError()
    if not _OPAQUE_NAME_PATTERN.fullmatch(opaque_name):
        raise WorktreeDeniedError()
    if "/" in opaque_name or "\\" in opaque_name or ".." in opaque_name:
        raise WorktreeDeniedError()
    if os.path.isabs(opaque_name):
        raise WorktreeDeniedError()
    return opaque_name


class SyntheticGitWorktreeAuthority(WorktreeAuthority):
    """Worktree real apenas em repositório Git temporário marcado para testes."""

    MARKER_FILENAME = ".harmonia_synthetic_git"

    def __init__(self, temp_root: Path) -> None:
        self._temp_root = temp_root.resolve()
        self._created: set[str] = set()

    @classmethod
    def bootstrap(cls, temp_root: Path) -> SyntheticGitWorktreeAuthority:
        temp_root.mkdir(parents=True, exist_ok=True)
        marker = temp_root / cls.MARKER_FILENAME
        marker.write_text("synthetic\n", encoding="utf-8")
        subprocess.run(
            ["git", "init"],
            cwd=temp_root,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "harmonia@test.local"],
            cwd=temp_root,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Harmonia Test"],
            cwd=temp_root,
            check=True,
            capture_output=True,
            text=True,
        )
        seed = temp_root / "README.md"
        seed.write_text("synthetic repo\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "README.md"],
            cwd=temp_root,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "seed"],
            cwd=temp_root,
            check=True,
            capture_output=True,
            text=True,
        )
        return cls(temp_root)

    def _assert_synthetic(self, repository_root: str) -> Path:
        root = Path(os.path.realpath(repository_root))
        marker = root / self.MARKER_FILENAME
        if not marker.is_file():
            raise WorktreeDeniedError()
        if not root.is_relative_to(self._temp_root):
            raise WorktreeDeniedError()
        return root

    def _assert_contained_destination(self, root: Path, destination: Path) -> Path:
        worktrees_dir = (root / ".harmonia_worktrees").resolve()
        worktrees_dir.mkdir(exist_ok=True)
        resolved = Path(os.path.realpath(destination))
        try:
            resolved.relative_to(worktrees_dir)
        except ValueError as exc:
            raise WorktreeDeniedError() from exc
        return resolved

    def create_worktree(
        self,
        *,
        repository_root: str,
        base_ref: str,
        opaque_name: str,
    ) -> str:
        root = self._assert_synthetic(repository_root)
        validated = _validate_opaque_name(opaque_name)
        worktrees_dir = root / ".harmonia_worktrees"
        worktrees_dir.mkdir(exist_ok=True)
        destination = worktrees_dir / validated
        if destination.exists() or destination.is_symlink():
            raise WorktreeDeniedError()
        resolved = self._assert_contained_destination(root, destination)
        subprocess.run(
            ["git", "worktree", "add", str(resolved), base_ref],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        final = str(Path(os.path.realpath(resolved)))
        self._assert_contained_destination(root, Path(final))
        self._created.add(final)
        return final

    def remove_worktree(self, worktree_path: str) -> None:
        resolved = str(Path(os.path.realpath(worktree_path)))
        if resolved not in self._created:
            raise WorktreeDeniedError()
        root = self._temp_root
        self._assert_contained_destination(root, Path(resolved))
        result = subprocess.run(
            ["git", "worktree", "remove", "--force", resolved],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise HarmoniaError(REASON_WORKTREE_CLEANUP_FAILED)
        self._created.discard(resolved)
