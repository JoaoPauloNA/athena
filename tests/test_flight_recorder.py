"""Deterministic tests for Athena Flight Recorder (Phase 1)."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from athena.bridge import run_subprocess
from athena.flight_recorder import (
    FlightRecorder,
    FlightRecorderSession,
    is_sensitive_env_name,
    sanitize_command,
    sanitize_env_names,
    sanitize_string,
)


def _read_events(logs_dir: Path) -> list[dict]:
    events: list[dict] = []
    for path in sorted(logs_dir.glob("**/events.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    return events


def test_sanitize_command_redacts_flag_values_and_inline_secrets():
    cmd = [
        "cli",
        "--api-key",
        "sk-live-secret",
        "Bearer abc123token",
        "--model",
        "gpt",
    ]
    sanitized = sanitize_command(cmd)
    assert sanitized[0] == "cli"
    assert sanitized[1] == "--api-key"
    assert sanitized[2] == "[REDACTED]"
    assert "[REDACTED]" in sanitized[3]
    assert sanitized[4] == "--model"
    assert sanitized[5] == "gpt"


def test_sanitize_env_names_only_lists_keys():
    env = {"PATH": "/bin", "OPENAI_API_KEY": "secret", "HOME": "/tmp"}
    names = sanitize_env_names(env)
    assert names == ["HOME", "OPENAI_API_KEY", "PATH"]
    assert "secret" not in json.dumps(names)


def test_is_sensitive_env_name():
    assert is_sensitive_env_name("OPENAI_API_KEY")
    assert is_sensitive_env_name("MY_TOKEN")
    assert not is_sensitive_env_name("PATH")


def test_sanitize_string_bearer_and_authorization():
    raw = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9"
    assert "[REDACTED]" in sanitize_string(raw)
    assert "eyJ" not in sanitize_string(raw)


def test_sanitize_string_password_and_cookie():
    assert "[REDACTED]" in sanitize_string("password=super-secret")
    assert "super-secret" not in sanitize_string("password=super-secret")
    assert "[REDACTED]" in sanitize_string("Cookie: session=abc123; Path=/")
    assert "abc123" not in sanitize_string("Cookie: session=abc123; Path=/")


def test_flight_recorder_writes_started_and_terminal_with_artifacts(tmp_path: Path):
    logs_dir = tmp_path / "logs"
    recorder = FlightRecorder(
        logs_dir=logs_dir,
        execution_id="exec-1",
        attempt_id="att-1",
        provider="fake",
        profile="default",
        transport="local",
    )
    recorder.record_attempt_started(
        command=["echo", "hi"],
        cwd=str(tmp_path),
        env={"PATH": "/bin"},
    )
    recorder.record_attempt_terminal(
        state="COMPLETED",
        exit_code=0,
        duration_s=0.1,
        stdout="hello stdout",
        stderr="hello stderr",
        command=["echo", "hi"],
        cwd=str(tmp_path),
        pid=42,
        pgid=42,
    )

    events = _read_events(logs_dir)
    assert any(e["event_type"] == "attempt_started" for e in events)
    terminal = next(e for e in events if e["event_type"] == "attempt_terminal")
    assert terminal["execution_id"] == "exec-1"
    assert terminal["attempt_id"] == "att-1"
    assert terminal["exit_code"] == 0
    assert terminal["stdout_size"] == len("hello stdout")
    assert terminal["stderr_size"] == len("hello stderr")
    assert terminal["stdout_hash"]
    assert terminal["stderr_hash"]

    stdout_path = logs_dir / "artifacts" / "exec-1" / "att-1" / "stdout.txt"
    stderr_path = logs_dir / "artifacts" / "exec-1" / "att-1" / "stderr.txt"
    assert stdout_path.read_text(encoding="utf-8") == "hello stdout"
    assert stderr_path.read_text(encoding="utf-8") == "hello stderr"


def test_flight_recorder_write_failure_is_non_fatal(tmp_path: Path, monkeypatch):
    logs_dir = tmp_path / "logs"

    def _raise_oserror(*_args, **_kwargs):
        raise OSError("simulated write failure")

    monkeypatch.setattr("athena.flight_recorder._append_line_synced", _raise_oserror)

    recorder = FlightRecorder(
        logs_dir=logs_dir,
        execution_id="exec-ro",
        attempt_id="att-ro",
        provider="fake",
    )
    recorder.record_attempt_started(command=["true"], cwd=None, env=None)
    recorder.record_attempt_terminal(
        state="FAILED",
        exit_code=1,
        duration_s=0.0,
        stdout="x",
        stderr="y",
    )
    assert recorder.warnings
    assert any("flight_recorder" in w for w in recorder.warnings)


def test_artifacts_and_jsonl_redact_secrets(tmp_path: Path):
    logs_dir = tmp_path / "logs"
    recorder = FlightRecorder(
        logs_dir=logs_dir,
        execution_id="sec-art",
        attempt_id="sec-att",
        provider="fake",
    )
    secret_stdout = "Authorization: Bearer leaked-stdout-token"
    secret_stderr = "password=leaked-password"
    recorder.record_attempt_terminal(
        state="FAILED",
        exit_code=1,
        duration_s=0.01,
        stdout=secret_stdout,
        stderr=secret_stderr,
        error="token=inline-leak",
        command=["cli", "--token", "flag-secret"],
    )

    stdout_path = logs_dir / "artifacts" / "sec-art" / "sec-att" / "stdout.txt"
    stderr_path = logs_dir / "artifacts" / "sec-art" / "sec-att" / "stderr.txt"
    stdout_text = stdout_path.read_text(encoding="utf-8")
    stderr_text = stderr_path.read_text(encoding="utf-8")
    assert "[REDACTED]" in stdout_text
    assert "leaked-stdout-token" not in stdout_text
    assert "[REDACTED]" in stderr_text
    assert "leaked-password" not in stderr_text

    events_blob = json.dumps(_read_events(logs_dir))
    assert "leaked-stdout-token" not in events_blob
    assert "leaked-password" not in events_blob
    assert "inline-leak" not in events_blob
    assert "flag-secret" not in events_blob


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_flight_recorder_sets_private_permissions(tmp_path: Path):
    logs_dir = tmp_path / "logs"
    recorder = FlightRecorder(
        logs_dir=logs_dir,
        execution_id="perm-exec",
        attempt_id="perm-att",
        provider="fake",
    )
    recorder.record_attempt_started(command=["echo", "ok"], cwd=None, env=None)
    recorder.record_attempt_terminal(
        state="COMPLETED",
        exit_code=0,
        duration_s=0.0,
        stdout="ok",
        stderr="",
    )

    events_path = next(logs_dir.glob("**/events.jsonl"))
    artifacts_dir = logs_dir / "artifacts" / "perm-exec" / "perm-att"
    stdout_path = artifacts_dir / "stdout.txt"

    assert stat.S_IMODE(os.stat(logs_dir).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(events_path.parent).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(artifacts_dir).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(events_path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(stdout_path).st_mode) == 0o600


def test_bridge_run_subprocess_emits_flight_events_and_artifacts(tmp_path: Path, monkeypatch):
    logs_dir = tmp_path / "flight-logs"
    monkeypatch.setenv("ATHENA_LOGS_DIR", str(logs_dir))

    result = run_subprocess(
        "synthetic",
        [
            sys.executable,
            "-c",
            "import sys; print('out'); print('err', file=sys.stderr)",
        ],
        execution_id="bridge-exec",
        attempt_id="bridge-att",
    )

    assert result.exit_code == 0
    events = _read_events(logs_dir)
    assert any(e["event_type"] == "attempt_started" for e in events)
    assert any(e["event_type"] == "attempt_terminal" for e in events)
    assert any(e["event_type"] == "state_transition" for e in events)

    stdout_path = logs_dir / "artifacts" / "bridge-exec" / "bridge-att" / "stdout.txt"
    stderr_path = logs_dir / "artifacts" / "bridge-exec" / "bridge-att" / "stderr.txt"
    assert stdout_path.exists()
    assert stderr_path.exists()
    assert "out" in stdout_path.read_text(encoding="utf-8")
    assert "err" in stderr_path.read_text(encoding="utf-8")


def test_bridge_prelaunch_failure_emits_terminal_event(tmp_path: Path, monkeypatch):
    logs_dir = tmp_path / "flight-logs"
    monkeypatch.setenv("ATHENA_LOGS_DIR", str(logs_dir))

    result = run_subprocess(
        "synthetic",
        ["__nonexistent_binary_athena_flight__"],
        execution_id="pre-exec",
        attempt_id="pre-att",
    )

    assert result.exit_code == 127
    events = _read_events(logs_dir)
    terminal = next(e for e in events if e["event_type"] == "attempt_terminal")
    assert terminal["state"] == "FAILED"
    assert terminal["exit_code"] == 127


def test_bridge_timeout_emits_terminal_event(tmp_path: Path, monkeypatch):
    logs_dir = tmp_path / "flight-logs"
    monkeypatch.setenv("ATHENA_LOGS_DIR", str(logs_dir))

    result = run_subprocess(
        "synthetic",
        [sys.executable, "-c", "import time; time.sleep(30)"],
        timeout=1,
        execution_id="to-exec",
        attempt_id="to-att",
    )

    assert result.timed_out is True
    assert result.exit_code == 124
    events = _read_events(logs_dir)
    terminal = next(e for e in events if e["event_type"] == "attempt_terminal")
    assert terminal["timed_out"] is True
    assert terminal["state"] in {"CANCELLED", "TERMINATION_UNCONFIRMED"}


def test_session_sanitizes_command_with_secrets_in_finalize(tmp_path: Path):
    logs_dir = tmp_path / "logs"
    session = FlightRecorderSession(
        logs_dir=logs_dir,
        execution_id="sec-exec",
        attempt_id="sec-att",
        provider="fake",
        profile=None,
        transport="local",
        command=["cli", "--api-key", "sk-secret"],
        cwd=None,
        env={"OPENAI_API_KEY": "hidden"},
    )

    class _FakeRecord:
        state = type("S", (), {"value": "FAILED"})()
        pid = None
        pgid = None

    class _FakeResult:
        exit_code = 1
        duration_s = 0.01
        stdout = ""
        stderr = ""
        error = "Bearer leaked-token"
        timed_out = False
        execution = {"state": "FAILED"}
        warnings: list[str] = []

    session.finalize_terminal(_FakeResult(), _FakeRecord())
    events = _read_events(logs_dir)
    started = next(e for e in events if e["event_type"] == "attempt_started")
    terminal = next(e for e in events if e["event_type"] == "attempt_terminal")
    assert started["command"][2] == "[REDACTED]"
    assert "OPENAI_API_KEY" in started["env_var_names"]
    assert "hidden" not in json.dumps(started)
    assert "[REDACTED]" in terminal["error"]
