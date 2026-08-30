"""Testes do gate da FATIA 0."""

from harness import p0_gate


def test_gate_is_importable_and_has_expected_stage_order() -> None:
    assert [stage.name for stage in p0_gate.STAGES] == ["lint", "boundaries", "p0"]
    assert p0_gate.STAGES[0].command == (
        "ruff",
        "check",
        ".",
        "--exclude",
        "athena/api_mode.py",
        "--exclude",
        "tests/test_api_mode.py",
    )
    assert p0_gate.STAGES[1].command == ("lint-imports",)
    assert p0_gate.STAGES[-1].command == (
        "pytest",
        "tests",
        "-m",
        "not regression",
        "--ignore",
        "tests/test_api_mode.py",
    )
