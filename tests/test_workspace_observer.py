"""Deterministic tests for WorkspaceChangeObserver (silent progress detection)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

from athena.bridge import run_subprocess
from athena.execution import ExecutionRecord, ExecutionState
from athena.flight_recorder import FlightRecorderSession
from athena.workspace_observer import (
    WorkspaceChangeObserver,
    WorkspaceDiff,
    WorkspaceSnapshot,
    _should_ignore_path,
    _snapshot_mtime,
)


class TestIgnorePaths:
    """Test path filtering logic."""

    def test_ignores_git_directories(self, tmp_path: Path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        assert _should_ignore_path(git_dir / "config", tmp_path)

    def test_ignores_pycache(self, tmp_path: Path):
        pycache = tmp_path / "__pycache__"
        pycache.mkdir()
        assert _should_ignore_path(pycache / "module.pyc", tmp_path)

    def test_ignores_node_modules(self, tmp_path: Path):
        nm = tmp_path / "node_modules"
        nm.mkdir()
        assert _should_ignore_path(nm / "package" / "index.js", tmp_path)

    def test_ignores_lock_files(self, tmp_path: Path):
        lock = tmp_path / "file.lock"
        assert _should_ignore_path(lock, tmp_path)

    def test_ignores_log_files(self, tmp_path: Path):
        log = tmp_path / "debug.log"
        assert _should_ignore_path(log, tmp_path)

    def test_allows_regular_files(self, tmp_path: Path):
        regular = tmp_path / "main.py"
        assert not _should_ignore_path(regular, tmp_path)

    def test_allows_regular_dirs(self, tmp_path: Path):
        src = tmp_path / "src"
        src.mkdir()
        assert not _should_ignore_path(src / "module.py", tmp_path)

    def test_rejects_paths_outside_workspace(self, tmp_path: Path):
        outside = Path("/tmp/other")
        assert _should_ignore_path(outside, tmp_path)


class TestSnapshotDiff:
    """Test snapshot comparison."""

    def test_detects_file_creation(self):
        snap1 = WorkspaceSnapshot(mtimes={"a.txt": 1.0})
        snap2 = WorkspaceSnapshot(mtimes={"a.txt": 1.0, "b.txt": 2.0})
        diff = snap2.diff_since(snap1)
        assert diff.created == ["b.txt"]
        assert not diff.deleted
        assert not diff.modified

    def test_detects_file_deletion(self):
        snap1 = WorkspaceSnapshot(mtimes={"a.txt": 1.0, "b.txt": 2.0})
        snap2 = WorkspaceSnapshot(mtimes={"a.txt": 1.0})
        diff = snap2.diff_since(snap1)
        assert diff.deleted == ["b.txt"]
        assert not diff.created
        assert not diff.modified

    def test_detects_file_modification(self):
        snap1 = WorkspaceSnapshot(mtimes={"a.txt": 1.0})
        snap2 = WorkspaceSnapshot(mtimes={"a.txt": 2.0})
        diff = snap2.diff_since(snap1)
        assert diff.modified == ["a.txt"]
        assert not diff.created
        assert not diff.deleted

    def test_no_changes(self):
        snap1 = WorkspaceSnapshot(mtimes={"a.txt": 1.0})
        snap2 = WorkspaceSnapshot(mtimes={"a.txt": 1.0})
        diff = snap2.diff_since(snap1)
        assert not diff.has_changes

    def test_to_event_data(self):
        snap1 = WorkspaceSnapshot(mtimes={})
        snap2 = WorkspaceSnapshot(
            mtimes={
                "a.txt": 1.0,
                "b.txt": 2.0,
                "c.txt": 3.0,
            }
        )
        diff = snap2.diff_since(snap1)
        data = diff.to_event_data()
        assert data["created_count"] == 3
        assert len(data["created_sample"]) == 3


class TestWorkspaceChangeObserver:
    """Test observer functionality."""

    def test_disabled_when_workspace_none(self):
        observer = WorkspaceChangeObserver(workspace=None)
        assert not observer.enabled

    def test_disabled_when_workspace_not_exists(self, tmp_path: Path):
        observer = WorkspaceChangeObserver(workspace=tmp_path / "nonexistent")
        assert not observer.enabled

    def test_enabled_when_workspace_valid(self, tmp_path: Path):
        observer = WorkspaceChangeObserver(workspace=tmp_path)
        assert observer.enabled

    def test_start_stop_idempotent(self, tmp_path: Path):
        observer = WorkspaceChangeObserver(workspace=tmp_path)
        observer.start()
        observer.start()
        observer.stop()
        observer.stop()

    def test_detects_file_creation_with_mock_record(self, tmp_path: Path):
        """Observer detects file creation and calls record.record_progress()."""
        progress_calls = []

        class MockRecord:
            def record_progress(self):
                progress_calls.append(time.monotonic())

        observer = WorkspaceChangeObserver(
            workspace=tmp_path,
            record=MockRecord(),
            poll_interval_s=0.05,
            debounce_s=0.01,
        )
        observer.start()
        time.sleep(0.1)

        (tmp_path / "newfile.txt").write_text("content")
        time.sleep(0.2)

        observer.stop()
        assert len(progress_calls) > 0

    def test_detects_file_modification_with_mock_record(self, tmp_path: Path):
        """Observer detects file modification."""
        progress_calls = []

        class MockRecord:
            def record_progress(self):
                progress_calls.append(time.monotonic())

        test_file = tmp_path / "existing.txt"
        test_file.write_text("v1")
        time.sleep(0.1)

        observer = WorkspaceChangeObserver(
            workspace=tmp_path,
            record=MockRecord(),
            poll_interval_s=0.05,
            debounce_s=0.01,
        )
        observer.start()
        time.sleep(0.1)

        test_file.write_text("v2")
        time.sleep(0.2)

        observer.stop()
        assert len(progress_calls) > 0

    def test_ignores_pycache_changes(self, tmp_path: Path):
        """Observer does not report __pycache__ changes."""
        progress_calls = []

        class MockRecord:
            def record_progress(self):
                progress_calls.append(time.monotonic())

        observer = WorkspaceChangeObserver(
            workspace=tmp_path,
            record=MockRecord(),
            poll_interval_s=0.05,
            debounce_s=0.01,
        )
        observer.start()
        time.sleep(0.1)

        pycache = tmp_path / "__pycache__"
        pycache.mkdir()
        (pycache / "module.pyc").write_bytes(b"pyc")
        time.sleep(0.2)

        observer.stop()
        assert len(progress_calls) == 0

    def test_ignores_git_changes(self, tmp_path: Path):
        """Observer does not report .git changes."""
        progress_calls = []

        class MockRecord:
            def record_progress(self):
                progress_calls.append(time.monotonic())

        observer = WorkspaceChangeObserver(
            workspace=tmp_path,
            record=MockRecord(),
            poll_interval_s=0.05,
            debounce_s=0.01,
        )
        observer.start()
        time.sleep(0.1)

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("config")
        time.sleep(0.2)

        observer.stop()
        assert len(progress_calls) == 0


class TestSilentAgentWithIdleTimeout:
    """Integration test: silent agent creating files doesn't timeout."""

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only")
    def test_silent_agent_creates_file_and_sleeps_beyond_idle_timeout(self, tmp_path: Path):
        """Agent creates file after delay, sleeps, but completes successfully.

        Without workspace observer, this would timeout. With it, progress is
        recorded via file creation, resetting the idle clock.
        """
        script = (
            "import os, time\n"
            "time.sleep(0.3)\n"
            f"open({str(tmp_path / 'output.txt')!r}, 'w').write('done')\n"
            "os.sync()\n"
            "time.sleep(1.5)\n"
        )

        record = ExecutionRecord(provider="test")
        flight = FlightRecorderSession(
            execution_id=record.execution_id,
            attempt_id=record.attempt_id,
            provider="test",
            profile=None,
            transport="local",
            command=["python", "-c", script],
            cwd=str(tmp_path),
            env=None,
        )

        result = run_subprocess(
            "test",
            [sys.executable, "-c", script],
            cwd=str(tmp_path),
            timeout=10,
            idle_timeout=2,
            execution_id=record.execution_id,
            attempt_id=record.attempt_id,
            on_execution_update=flight.wrap_on_update(None),
        )

        assert result.exit_code == 0
        assert (tmp_path / "output.txt").exists()
        assert not result.timed_out


class TestSnapshot:
    """Test snapshot generation."""

    def test_snapshot_builds_mtime_map(self, tmp_path: Path):
        """_snapshot_mtime builds complete map of file mtimes."""
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "c.txt").write_text("c")

        snapshot = _snapshot_mtime(tmp_path, tmp_path)
        assert "a.txt" in snapshot
        assert "b.txt" in snapshot
        assert str(Path("subdir") / "c.txt") in snapshot

    def test_snapshot_excludes_ignored_files(self, tmp_path: Path):
        """_snapshot_mtime excludes .git, __pycache__, etc."""
        (tmp_path / "a.txt").write_text("a")
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("config")
        pycache = tmp_path / "__pycache__"
        pycache.mkdir()
        (pycache / "module.pyc").write_bytes(b"pyc")

        snapshot = _snapshot_mtime(tmp_path, tmp_path)
        assert "a.txt" in snapshot
        assert ".git" not in str(snapshot.keys())
        assert "__pycache__" not in str(snapshot.keys())
