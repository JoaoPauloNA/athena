"""Testes do gate da FATIA 0."""

from harness import p0_gate


def test_gate_is_importable_and_has_expected_stage_order() -> None:
    assert [stage.name for stage in p0_gate.STAGES] == ["lint", "boundaries", "p0"]
