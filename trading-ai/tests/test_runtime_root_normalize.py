"""EZRAS_RUNTIME_ROOT pathological values."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_literal_tilde_segment_resolves_to_home_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EZRAS_RUNTIME_ROOT", raising=False)
    bad = str(Path("/tmp") / "~" / "ezras-runtime")
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", bad)
    from trading_ai.runtime_paths import ezras_runtime_root

    assert ezras_runtime_root() == (Path.home() / "ezras-runtime").resolve()
