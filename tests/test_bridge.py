"""Regression tests for provider subprocess transport."""

import sys

from athena.bridge import run_subprocess


def test_run_subprocess_preserves_individual_streams():
    result = run_subprocess(
        "fake",
        [
            sys.executable,
            "-c",
            "import sys; print('final report'); print('ERROR transport', file=sys.stderr)",
        ],
    )

    assert result.stdout == "final report"
    assert result.stderr == "ERROR transport"
    assert result.output == "final report\nERROR transport"
