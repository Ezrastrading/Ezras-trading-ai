"""Package metadata consistency (Python version)."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_pyproject_requires_python_311_plus() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.11"' in text


def test_python_version_file_matches() -> None:
    root = Path(__file__).resolve().parents[1]
    pinned = (root / ".python-version").read_text(encoding="utf-8").strip()
    assert pinned == "3.11.8"


@pytest.mark.skipif(
    __import__("sys").version_info[:2] < (3, 11),
    reason="Repo requires Python 3.11+",
)
def test_runtime_is_311_plus() -> None:
    import sys

    assert sys.version_info[:2] >= (3, 11)
