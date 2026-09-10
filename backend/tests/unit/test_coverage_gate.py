"""Tests for the independent coverage gate."""

from pathlib import Path

import pytest
from scripts.check_coverage import coverage_metrics, function_lines, percentage


def test_percentage_handles_empty_and_nonempty_totals() -> None:
    assert percentage(0, 0) == 100.0
    assert percentage(3, 4) == 75.0


def test_function_lines_finds_sync_and_async_functions(tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    source.write_text(
        "def one():\n    return 1\n\nasync def two():\n    return 2\n\ndef protocol(): ...\n"
    )
    assert function_lines(source) == [{2}, {5}]


def test_coverage_metrics_rejects_invalid_report() -> None:
    with pytest.raises(TypeError, match="invalid coverage report"):
        coverage_metrics({"totals": [], "files": {}})
