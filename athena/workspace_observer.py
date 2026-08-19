"""Observe workspace changes to detect silent progress during idle timeouts.

When a subprocess works without producing stdout/stderr (e.g., creating files,
writing to databases), the idle_timeout deadline would normally fire and
terminate the task. This module observes the workspace (working_directory) for
meaningful changes, records progress events, and generates Flight Recorder
events so the execution doesn't incorrectly time out due to silent work.

Progress signals: file creation, modification, deletion (recursive, ignoring
known noise like .git, __pycache__, node_modules, dist, build, .cache, browser
profiles, logs).
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

_DEFAULT_POLL_INTERVAL_S = 0.5
_DEFAULT_DEBOUNCE_S = 0.1
_MIN_POLL_S = 0.05
_MAX_POLL_S = 5.0

_IGNORED_DIRS = frozenset({
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "node_modules",
    ".npm",
    "dist",
    "build",
    ".build",
    ".cache",
    ".venv",
    "venv",
    ".env",
    ".eggs",
    "*.egg-info",
    ".tox",
    ".coverage",
    ".idea",
    ".vscode",
    ".DS_Store",
    "target",  # Rust/Maven
    "bin",
    "obj",
})

_IGNORED_FILE_PATTERNS = frozenset({
    ".git",
    ".lock",
    ".tmp",
    ".log",
    ".swp",
    ".swo",
    "~",
    ".pyc",
    ".pyo",
})

_BROWSER_PROFILE_DIRS = frozenset({
    "chrome-profile",
    "chromium-profile",
    "firefox-profile",
    "safari-profile",
    ".chromium",
    ".firefox",
})


def _should_ignore_path(path: Path, workspace: Path) -> bool:
    """Check if path should be ignored when observing workspace changes."""
    try:
        rel = path.relative_to(workspace)
    except ValueError:
        return True

    parts = rel.parts
    for part in parts:
        if part in _IGNORED_DIRS:
            return True
        if part in _BROWSER_PROFILE_DIRS:
            return True
        if any(part.endswith(f) for f in _IGNORED_FILE_PATTERNS):
            return True

    return False


def _snapshot_mtime(path: Path, workspace: Path) -> dict[str, float]:
    """Build a map of relative path -> mtime for all files under path, ignoring noise."""
    snapshot = {}
    try:
        for root, dirs, files in os.walk(path, topdown=True):
            root_p = Path(root)
            dirs[:] = [
                d for d in dirs
                if not _should_ignore_path(root_p / d, workspace)
            ]
            for fname in files:
                fpath = root_p / fname
                if _should_ignore_path(fpath, workspace):
                    continue
                try:
                    mtime = fpath.stat().st_mtime
                    rel = fpath.relative_to(workspace)
                    snapshot[str(rel)] = mtime
                except (OSError, ValueError):
                    pass
    except (OSError, ValueError):
        pass
    return snapshot


@dataclass
class WorkspaceSnapshot:
    """Immutable snapshot of workspace state at a moment in time."""
    mtimes: dict[str, float]
    captured_at_monotonic: float = field(default_factory=time.monotonic)

    def diff_since(self, prior: WorkspaceSnapshot) -> WorkspaceDiff:
        """Compare this snapshot to an earlier one, returning changes."""
        created = set(self.mtimes.keys()) - set(prior.mtimes.keys())
        deleted = set(prior.mtimes.keys()) - set(self.mtimes.keys())
        modified = {
            path for path in self.mtimes.keys() & prior.mtimes.keys()
            if self.mtimes[path] != prior.mtimes[path]
        }
        return WorkspaceDiff(
            created=sorted(created),
            deleted=sorted(deleted),
            modified=sorted(modified),
        )


@dataclass
class WorkspaceDiff:
    """Changes detected between two snapshots."""
    created: list[str]
    deleted: list[str]
    modified: list[str]

    @property
    def has_changes(self) -> bool:
        return bool(self.created or self.deleted or self.modified)

    def to_event_data(self) -> dict[str, Any]:
        """Convert to Flight Recorder event payload."""
        return {
            "created_count": len(self.created),
            "deleted_count": len(self.deleted),
            "modified_count": len(self.modified),
            "created_sample": self.created[:5],
            "deleted_sample": self.deleted[:5],
            "modified_sample": self.modified[:5],
        }


class WorkspaceChangeObserver:
    """Monitor a workspace directory for changes while a subprocess runs.

    Thread-safe; polling happens on a background thread. Calls record_progress()
    and emits Flight Recorder events on every significant change, preventing
    idle_timeout from firing on silent but observable work.
    """

    def __init__(
        self,
        *,
        workspace: str | Path | None = None,
        record: Any | None = None,
        flight: Any | None = None,
        poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S,
        debounce_s: float = _DEFAULT_DEBOUNCE_S,
    ) -> None:
        if workspace is None or not workspace:
            self.workspace = None
            self._enabled = False
        else:
            self.workspace = Path(workspace)
            if not self.workspace.is_dir():
                self._enabled = False
            else:
                self._enabled = True

        self.record = record
        self.flight = flight

        poll_interval_s = max(_MIN_POLL_S, min(_MAX_POLL_S, float(poll_interval_s)))
        self.poll_interval_s = poll_interval_s
        self.debounce_s = max(0.0, float(debounce_s))

        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._last_snapshot: WorkspaceSnapshot | None = None
        self._last_progress_at: float | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def start(self) -> None:
        """Begin monitoring in a background thread."""
        if not self._enabled or self.workspace is None:
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            initial = _snapshot_mtime(self.workspace, self.workspace)
            self._last_snapshot = WorkspaceSnapshot(mtimes=initial)
            self._thread = threading.Thread(target=self._poll_loop, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        """Stop monitoring (blocks until thread exits)."""
        if not self._enabled:
            return
        self._stop_event.set()
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._thread.join(timeout=5.0)
            self._thread = None

    def _poll_loop(self) -> None:
        """Background polling thread main loop."""
        while not self._stop_event.is_set():
            try:
                self._poll_once()
            except Exception:
                pass
            if not self._stop_event.wait(self.poll_interval_s):
                pass

    def _poll_once(self) -> None:
        """Single polling iteration: check for changes and emit progress events."""
        if self.workspace is None or not self.workspace.is_dir():
            return

        current = _snapshot_mtime(self.workspace, self.workspace)
        now = time.monotonic()

        with self._lock:
            if self._last_snapshot is None:
                self._last_snapshot = WorkspaceSnapshot(mtimes=current)
                return

            diff = current_snap = WorkspaceSnapshot(mtimes=current)
            diff = diff.diff_since(self._last_snapshot)

            if not diff.has_changes:
                return

            if self._last_progress_at is not None:
                time_since_last_s = now - self._last_progress_at
                if time_since_last_s < self.debounce_s:
                    return

            self._last_snapshot = current_snap
            self._last_progress_at = now

        self._emit_progress(diff)

    def _emit_progress(self, diff: WorkspaceDiff) -> None:
        """Emit progress signals and Flight Recorder events."""
        if self.record is not None:
            try:
                self.record.record_progress()
            except Exception:
                pass

        if self.flight is not None:
            try:
                self.flight.record_event(
                    "workspace_change_observed",
                    **diff.to_event_data(),
                )
            except Exception:
                pass
