"""Master smoke harness produces durable control artifacts."""

from __future__ import annotations

import json
import os

import pytest


@pytest.fixture
def rt(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    os.environ.setdefault("NTE_EXECUTION_MODE", "paper")
    os.environ.setdefault("NTE_LIVE_TRADING_ENABLED", "false")
    os.environ.setdefault("COINBASE_EXECUTION_ENABLED", "false")
    return tmp_path


def test_master_smoke_end_to_end(rt) -> None:
    from trading_ai.runtime.master_smoke import run_master_smoke

    out = run_master_smoke(runtime_root=rt, cycles=16)
    assert out.get("ok") is True
    p = rt / "data" / "control" / "master_smoke.json"
    assert p.is_file()
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert doc.get("truth_version") == "master_smoke_v2"
    assert (rt / "data" / "control" / "regression_drift.json").is_file()
