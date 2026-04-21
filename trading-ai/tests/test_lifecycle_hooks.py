"""Lifecycle hooks write trace logs and scoped stubs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def rt(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    return tmp_path


def test_on_validation_writes_control_bundle(rt: Path) -> None:
    from trading_ai.multi_avenue.lifecycle_hooks import on_validation

    out = on_validation(runtime_root=rt)
    assert out["status"] == "ok"
    assert (rt / "data/control/multi_avenue_status_matrix.json").is_file()
    log = json.loads((rt / "data/control/lifecycle_hook_log.json").read_text(encoding="utf-8"))
    assert any(e.get("hook") == "on_validation" for e in log.get("events", []))


def test_on_trade_open_writes_ratio_stub(rt: Path) -> None:
    from trading_ai.multi_avenue.lifecycle_hooks import on_trade_open

    r = on_trade_open(
        {"trade_id": "t1", "outlet": "kalshi", "avenue_id": "B", "gate_id": "gate_b"},
        runtime_root=rt,
    )
    assert r.get("status") == "ok"
    p = rt / "data/review/avenues/B/gates/gate_b/ratio_view/ratio_context.json"
    assert p.is_file()
