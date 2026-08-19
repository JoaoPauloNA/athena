"""Athena Flight Recorder — append-only forensic log per provider attempt.

Phase 1: durable JSONL events + stdout/stderr artifacts under ~/.athena/logs
(or ATHENA_LOGS_DIR). Write failures are non-fatal; warnings are surfaced to
callers via FlightRecorderSession.warnings.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from athena.config import LOGS_DIR

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None  # type: ignore[assignment]

# Env var names that must never be logged with values (names only).
_SENSITIVE_ENV_RE = re.compile(
    r"(api[_-]?key|secret|token|password|passwd|credential|auth|cookie|bearer|private)",
    re.IGNORECASE,
)

# Flag-style CLI args whose following value should be redacted.
_SENSITIVE_ARG_FLAGS = frozenset(
    {
        "--api-key",
        "--api_key",
        "--token",
        "--access-token",
        "--access_token",
        "--password",
        "--secret",
        "--authorization",
        "--auth",
        "--cookie",
        "--bearer",
    }
)

# Inline patterns in arbitrary strings (headers, cookies, credentials).
_INLINE_SECRET_RE = re.compile(
    r"(Authorization\s*[:=]\s*(?:Bearer\s+)?[A-Za-z0-9._\-+/=]+|"
    r"Bearer\s+[A-Za-z0-9._\-+/=]+|"
    r"api[_-]?key\s*[:=]\s*[^\s,;]+|"
    r"token\s*[:=]\s*[^\s,;]+|"
    r"password\s*[:=]\s*[^\s,;]+|"
    r"passwd\s*[:=]\s*[^\s,;]+|"
    r"cookie\s*[:=]\s*[^\s,;]+|"
    r"Cookie\s*:\s*[^\r\n;]+)",
    re.IGNORECASE,
)

_DIR_MODE = stat.S_IRWXU  # 0700
_FILE_MODE = stat.S_IRUSR | stat.S_IWUSR  # 0600


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_sensitive_env_name(name: str) -> bool:
    return bool(_SENSITIVE_ENV_RE.search(name))


def sanitize_env_names(env: dict[str, str] | None) -> list[str]:
    """Return sorted env var names only — never values."""
    if not env:
        return []
    return sorted(env.keys())


def sanitize_string(value: str) -> str:
    """Redact inline secrets from a single string."""
    return _INLINE_SECRET_RE.sub("[REDACTED]", value)


def _sanitize_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    command = out.get("command")
    if isinstance(command, list):
        out["command"] = sanitize_command(command)
    return _sanitize_json_value(out)


def _sanitize_json_value(value: Any) -> Any:
    """Recursively sanitize string leaves in event payloads."""
    if isinstance(value, str):
        return sanitize_string(value)
    if isinstance(value, list):
        return [_sanitize_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize_json_value(item) for key, item in value.items()}
    return value


def _ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, _DIR_MODE)
    except OSError:
        pass


def _set_private_file(path: Path) -> None:
    try:
        os.chmod(path, _FILE_MODE)
    except OSError:
        pass


def _acquire_file_lock(fh: TextIO) -> None:
    try:
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        elif msvcrt is not None:
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
    except (OSError, AttributeError, ValueError):
        pass


def _release_file_lock(fh: TextIO) -> None:
    try:
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        elif msvcrt is not None:
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    except (OSError, AttributeError, ValueError):
        pass


def _append_line_synced(path: Path, line: str) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        _acquire_file_lock(fh)
        try:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            _release_file_lock(fh)
    _set_private_file(path)


def sanitize_command(command: list[str]) -> list[str]:
    """Return a copy of argv with sensitive flag values and inline secrets redacted."""
    if not command:
        return []
    out: list[str] = []
    skip_next = False
    for i, raw in enumerate(command):
        if skip_next:
            skip_next = False
            out.append("[REDACTED]")
            continue
        token = str(raw)
        lower = token.lower()
        if lower in _SENSITIVE_ARG_FLAGS:
            out.append(token)
            if i + 1 < len(command):
                skip_next = True
            continue
        out.append(sanitize_string(token))
    return out


class FlightRecorder:
    """Append-only JSONL writer + artifact store for one attempt."""

    def __init__(
        self,
        *,
        logs_dir: Path | None = None,
        execution_id: str,
        attempt_id: str,
        provider: str,
        profile: str | None = None,
        transport: str = "local",
    ) -> None:
        if logs_dir is None:
            logs_dir = resolve_logs_dir()
        self.logs_dir = Path(logs_dir)
        _ensure_private_dir(self.logs_dir)
        _ensure_private_dir(self.logs_dir / "artifacts")
        self.execution_id = execution_id
        self.attempt_id = attempt_id
        self.provider = provider
        self.profile = profile
        self.transport = transport
        self._lock = threading.Lock()
        self.warnings: list[str] = []
        self._last_transition_key: tuple[str | None, str, str | None] | None = None

    def artifacts_dir(self) -> Path:
        return self.logs_dir / "artifacts" / self.execution_id / self.attempt_id

    def _events_path(self) -> Path:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.logs_dir / day / "events.jsonl"

    def _report_write_failure(self, operation: str, exc: BaseException) -> None:
        msg = f"flight_recorder_{operation}_failed: {type(exc).__name__}"
        self.warnings.append(msg)

    def _append_event(self, payload: dict[str, Any]) -> None:
        safe_payload = _sanitize_event_payload(payload)
        line = json.dumps(safe_payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        path = self._events_path()
        try:
            _ensure_private_dir(path.parent)
            _append_line_synced(path, line)
        except OSError as exc:
            self._report_write_failure("event_write", exc)

    def _base_event(self, event_type: str) -> dict[str, Any]:
        return {
            "timestamp_utc": _utc_now_iso(),
            "event_type": event_type,
            "execution_id": self.execution_id,
            "attempt_id": self.attempt_id,
            "provider": self.provider,
            "profile": self.profile,
            "transport": self.transport,
        }

    def record_event(self, event_type: str, **fields: Any) -> None:
        payload = self._base_event(event_type)
        payload.update(fields)
        with self._lock:
            self._append_event(payload)

    def record_state_transition(
        self,
        snapshot: dict[str, Any],
        *,
        from_state: str | None,
        to_state: str,
        reason: str | None = None,
    ) -> None:
        key = (from_state, to_state, reason)
        if key == self._last_transition_key:
            return
        self._last_transition_key = key
        self.record_event(
            "state_transition",
            state=to_state,
            from_state=from_state,
            to_state=to_state,
            reason=reason,
            pid=snapshot.get("pid"),
            pgid=snapshot.get("pgid"),
        )

    def on_execution_update(self, snapshot: dict[str, Any]) -> None:
        history = snapshot.get("history") or []
        if not history:
            return
        last = history[-1]
        self.record_state_transition(
            snapshot,
            from_state=last.get("from_state"),
            to_state=last.get("to_state") or snapshot.get("state"),
            reason=last.get("reason"),
        )

    def record_attempt_started(
        self,
        *,
        command: list[str],
        cwd: str | None,
        env: dict[str, str] | None,
    ) -> None:
        self.record_event(
            "attempt_started",
            state="STARTING",
            command=sanitize_command(command),
            cwd=cwd,
            env_var_names=sanitize_env_names(env),
        )

    def _write_artifact(self, name: str, content: str) -> tuple[str | None, int, str | None]:
        sanitized = sanitize_string(content)
        data = sanitized.encode("utf-8", errors="replace")
        size = len(data)
        digest = _sha256_hex(data)
        path = self.artifacts_dir() / name
        try:
            _ensure_private_dir(path.parent)
            path.write_bytes(data)
            _set_private_file(path)
            return digest, size, str(path)
        except OSError as exc:
            self._report_write_failure(f"artifact_{name}", exc)
            return digest, size, None

    def record_attempt_terminal(
        self,
        *,
        state: str,
        exit_code: int | None,
        duration_s: float,
        stdout: str | None = None,
        stderr: str | None = None,
        error: str | None = None,
        timed_out: bool = False,
        command: list[str] | None = None,
        cwd: str | None = None,
        pid: int | None = None,
        pgid: int | None = None,
    ) -> None:
        stdout_text = stdout or ""
        stderr_text = stderr or ""
        stdout_hash, stdout_size, stdout_path = self._write_artifact("stdout.txt", stdout_text)
        stderr_hash, stderr_size, stderr_path = self._write_artifact("stderr.txt", stderr_text)
        fields: dict[str, Any] = {
            "state": state,
            "exit_code": exit_code,
            "duration_s": duration_s,
            "stdout_hash": stdout_hash,
            "stdout_size": stdout_size,
            "stderr_hash": stderr_hash,
            "stderr_size": stderr_size,
            "stdout_artifact": stdout_path,
            "stderr_artifact": stderr_path,
            "timed_out": timed_out,
        }
        if error is not None:
            fields["error"] = sanitize_string(error)
        if command is not None:
            fields["command"] = sanitize_command(command)
        if cwd is not None:
            fields["cwd"] = cwd
        if pid is not None:
            fields["pid"] = pid
        if pgid is not None:
            fields["pgid"] = pgid
        self.record_event("attempt_terminal", **fields)


class FlightRecorderSession:
    """Bridge integration helper: one session per run_subprocess / run_with_pty call."""

    def __init__(
        self,
        *,
        logs_dir: Path | None = None,
        execution_id: str,
        attempt_id: str,
        provider: str,
        profile: str | None,
        transport: str,
        command: list[str],
        cwd: str | None,
        env: dict[str, str] | None,
    ) -> None:
        self.recorder = FlightRecorder(
            logs_dir=logs_dir,
            execution_id=execution_id,
            attempt_id=attempt_id,
            provider=provider,
            profile=profile,
            transport=transport,
        )
        self.command = list(command)
        self.cwd = cwd
        self.env = env
        self._terminal_recorded = False
        self.recorder.record_attempt_started(
            command=self.command,
            cwd=cwd,
            env=env,
        )

    @property
    def warnings(self) -> list[str]:
        return self.recorder.warnings

    def wrap_on_update(
        self,
        existing: Any | None,
    ) -> Any:
        def combined(snapshot: dict[str, Any]) -> None:
            self.recorder.on_execution_update(snapshot)
            if existing is not None:
                try:
                    existing(snapshot)
                except Exception:
                    pass

        return combined

    def finalize_terminal(
        self,
        result: Any,
        record: Any,
        *,
        stdout: str | None = None,
        stderr: str | None = None,
    ) -> None:
        if self._terminal_recorded:
            return
        self._terminal_recorded = True
        execution = result.execution or {}
        self.recorder.record_attempt_terminal(
            state=execution.get("state") or record.state.value,
            exit_code=result.exit_code,
            duration_s=result.duration_s,
            stdout=stdout if stdout is not None else result.stdout,
            stderr=stderr if stderr is not None else result.stderr,
            error=result.error,
            timed_out=bool(result.timed_out),
            command=self.command,
            cwd=self.cwd,
            pid=record.pid,
            pgid=record.pgid,
        )
        for warning in self.warnings:
            if warning not in result.warnings:
                result.warnings.append(warning)

    def finalize_prelaunch(
        self,
        result: Any,
        record: Any,
    ) -> None:
        self.finalize_terminal(result, record, stdout="", stderr="")


def resolve_logs_dir(explicit: Path | str | None = None) -> Path:
    if explicit is not None:
        return Path(explicit)
    return Path(os.environ.get("ATHENA_LOGS_DIR", str(LOGS_DIR)))
