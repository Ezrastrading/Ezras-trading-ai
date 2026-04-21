"""
Non-live hardening smoke: exercises duplicate window, mirror, intelligence hook, import audit script.
Does not place real trades.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


def test_hardening_control_artifacts_exist() -> None:
    root = Path(__file__).resolve().parents[1]
    for name in (
        "final_hardening_audit.json",
        "final_hardening_audit.txt",
        "module_load_audit.json",
        "module_load_audit.txt",
    ):
        p = root / "data" / "control" / name
        assert p.is_file(), name


def test_module_load_audit_json_valid() -> None:
    root = Path(__file__).resolve().parents[1]
    j = json.loads((root / "data" / "control" / "module_load_audit.json").read_text(encoding="utf-8"))
    assert j.get("all_ok") is True
    assert isinstance(j.get("modules"), list)


@pytest.mark.parametrize(
    "fn",
    [
        "trading_ai.safety.duplicate_trade_window.parse_duplicate_trade_window_from_env",
        "trading_ai.prelive.execution_mirror.run",
        "trading_ai.intelligence.integration.live_hooks.record_shark_submit_outcome",
    ],
)
def test_critical_symbols_importable(fn: str) -> None:
    mod_name, _, attr = fn.rpartition(".")
    m = importlib.import_module(mod_name)
    assert callable(getattr(m, attr))
