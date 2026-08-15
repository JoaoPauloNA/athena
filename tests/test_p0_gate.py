from __future__ import annotations

import json
from pathlib import Path

import harness.p0_gate as p0_gate


class _FakeCompleted:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeTempDir:
    def __init__(self, path: Path) -> None:
        self._path = path

    def __enter__(self) -> str:
        self._path.mkdir(parents=True, exist_ok=True)
        return str(self._path)

    def __exit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001
        return False


def test_main_success_writes_sanitized_schema_and_uses_temp_env(monkeypatch, tmp_path):
    output = tmp_path / "result.json"
    temp_data_dir = tmp_path / "athena_data"
    calls: list[dict] = []

    def fake_run(argv, capture_output, text, check, env):  # noqa: ANN001
        calls.append({"argv": tuple(argv), "env": dict(env)})
        return _FakeCompleted(returncode=0, stdout="collected 7 items\n", stderr="")

    monkeypatch.setattr(p0_gate.subprocess, "run", fake_run)
    monkeypatch.setattr(p0_gate.tempfile, "TemporaryDirectory", lambda prefix: _FakeTempDir(temp_data_dir))
    monkeypatch.setattr(
        p0_gate,
        "_utc_now",
        lambda: p0_gate.datetime(2026, 8, 14, 21, 0, 0, tzinfo=p0_gate.timezone.utc),
    )
    monkeypatch.setattr(p0_gate.sys, "argv", ["p0_gate.py", "--output", str(output)])

    exit_code = p0_gate.main()

    assert exit_code == 0
    assert output.exists()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == p0_gate.SCHEMA_VERSION
    assert payload["overall_status"] == "passed"
    assert payload["python"] == f"{p0_gate.sys.version_info.major}.{p0_gate.sys.version_info.minor}"
    assert payload["support_classification"]["windows"] == "NOT_GUARANTEED"
    assert payload["support_classification"]["posix"] == "LOCAL_CONTROLLED_ONLY"
    assert len(payload["stages"]) == len(p0_gate._build_stages())

    for call in calls:
        assert call["env"]["ATHENA_SKIP_AUTODISCOVERY"] == "1"
        assert Path(call["env"]["ATHENA_DATA_DIR"]) == temp_data_dir
        assert temp_data_dir.exists()
        assert tuple(call["argv"][:2]) == (p0_gate.sys.executable, "-m")

    for stage in payload["stages"]:
        assert set(stage.keys()) <= {"id", "status", "exit_code", "duration_s", "test_count"}

    serialized = json.dumps(payload, ensure_ascii=True)
    forbidden_snippets = [
        "command",
        "stdout",
        "stderr",
        "prompt",
        "response",
        "TOKEN=",
        "ATHENA_SKIP_AUTODISCOVERY",
        str(tmp_path),
    ]
    for needle in forbidden_snippets:
        assert needle not in serialized


def test_main_failure_keeps_json_and_returns_nonzero(monkeypatch, tmp_path):
    output = tmp_path / "failed.json"
    temp_data_dir = tmp_path / "athena_data"
    state = {"i": 0}

    def fake_run(argv, capture_output, text, check, env):  # noqa: ANN001, ARG001
        state["i"] += 1
        if state["i"] == 1:
            return _FakeCompleted(returncode=1, stdout="lint failed", stderr="E101")
        return _FakeCompleted(returncode=0, stdout="collected 3 items\n", stderr="")

    monkeypatch.setattr(p0_gate.subprocess, "run", fake_run)
    monkeypatch.setattr(p0_gate.tempfile, "TemporaryDirectory", lambda prefix: _FakeTempDir(temp_data_dir))
    monkeypatch.setattr(p0_gate.sys, "argv", ["p0_gate.py", "--output", str(output)])

    exit_code = p0_gate.main()

    assert exit_code == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["overall_status"] == "failed"
    failed = [stage for stage in payload["stages"] if stage["status"] == "failed"]
    assert failed
    test_stages = [stage for stage in payload["stages"] if stage["id"].startswith("tests_")]
    assert test_stages
    assert any("test_count" in stage for stage in test_stages)


def test_default_output_path_uses_results_dir_and_timestamp(monkeypatch):
    fake_now = p0_gate.datetime(2026, 8, 14, 21, 10, 9, tzinfo=p0_gate.timezone.utc)
    path = p0_gate._default_output_path(fake_now)
    assert str(path).startswith(str(p0_gate.RESULTS_DIR))
    assert path.name == "p0-gate-20260814T211009Z.json"
