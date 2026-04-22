from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


def test_gate_b_scanner_cycle_marks_active_and_emits_candidates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Proves structural non-emptiness comes from scanner execution, not placeholder scaffolds.

    We mock Coinbase public tickers to force at least one passing symbol, then assert:
    - scanner_metadata.json says active_scanners_present=true
    - candidate_queue.json gets items
    """
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("GATE_B_LIVE_EXECUTION_ENABLED", "true")

    # Mock public ticker endpoint used by gate_b_gainers_selection.
    def _fake_ticker(_path: str) -> dict:
        now = time.time()
        return {
            "product_id": "BTC-USD",
            "price": "50000",
            "best_bid": "49995",
            "best_ask": "50005",
            "time": now,
        }

    monkeypatch.setattr("trading_ai.shark.outlets.coinbase._brokerage_public_request", _fake_ticker)

    from trading_ai.multi_avenue.lifecycle_hooks import on_scanner_cycle_export

    rep = on_scanner_cycle_export(runtime_root=tmp_path)
    assert rep.get("status") == "ok"

    meta = tmp_path / "data" / "review" / "avenues" / "B" / "gates" / "gate_b" / "scanner_metadata.json"
    assert meta.is_file()
    m = json.loads(meta.read_text(encoding="utf-8"))
    assert m.get("active_scanners_present") is True

    cq = tmp_path / "shark" / "memory" / "global" / "candidate_queue.json"
    assert cq.is_file()
    c = json.loads(cq.read_text(encoding="utf-8"))
    assert len(c.get("items") or []) > 0

