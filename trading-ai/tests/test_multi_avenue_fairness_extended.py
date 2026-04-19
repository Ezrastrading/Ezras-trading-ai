"""Gap 3 — avenue rollups expose parity signals (USD vs play money, hard stops, anomalies)."""

from __future__ import annotations

import pytest

from trading_ai.global_layer.ai_review_packet_builder import build_review_packet
from trading_ai.global_layer.review_storage import ReviewStorage
from trading_ai.global_layer.trade_truth import avenue_fairness_rollups
from trading_ai.nte.memory.store import MemoryStore


def test_avenue_rollups_split_play_money_and_usd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("TRADE_DATABANK_MEMORY_ROOT", str(tmp_path / "databank"))
    (tmp_path / "databank").mkdir(parents=True)
    ms = MemoryStore()
    ms.ensure_defaults()
    ms.append_trade({"trade_id": "u1", "avenue": "coinbase", "net_pnl_usd": 1.0, "route_bucket": "x"})
    ms.append_trade({"trade_id": "p1", "avenue": "manifold", "net_pnl_usd": 0.5, "unit": "play_money", "route_bucket": "x"})
    from trading_ai.global_layer.trade_truth import load_federated_trades

    trades, _ = load_federated_trades(nte_store=ms)
    roll = avenue_fairness_rollups(trades)["by_avenue"]
    assert roll["coinbase"]["play_money_trade_count"] == 0
    assert roll["coinbase"]["usd_labeled_trade_count"] >= 1
    assert roll["manifold"]["play_money_trade_count"] >= 1


def test_packet_truth_includes_databank_root_and_avenue_fairness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("TRADE_DATABANK_MEMORY_ROOT", str(tmp_path / "databank"))
    (tmp_path / "databank").mkdir(parents=True)
    ms = MemoryStore()
    ms.ensure_defaults()
    ms.append_trade({"trade_id": "k1", "avenue": "coinbase", "net_pnl_usd": 2.0, "route_bucket": "y"})
    st = ReviewStorage()
    st.ensure_review_files()
    pkt = build_review_packet(storage=st)
    pt = pkt.get("packet_truth") or {}
    assert pt.get("databank_root")
    assert "coinbase" in (pt.get("avenue_fairness") or {})
