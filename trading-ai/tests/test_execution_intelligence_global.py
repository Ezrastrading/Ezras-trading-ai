"""Execution intelligence layer + global review integration."""

from __future__ import annotations

import time

import pytest

from trading_ai.global_layer.review_schema import validate_claude_output
from trading_ai.intelligence.avenue_performance import compute_avenue_performance
from trading_ai.intelligence.capital_allocator import optimize_capital_allocation
from trading_ai.intelligence.scaling_engine import generate_scaling_signal


def test_avenue_performance_from_trades_only():
    now = time.time()
    trades = [
        {
            "avenue": "coinbase",
            "net_pnl_usd": 10.0,
            "logged_at": now - 3600,
        },
        {
            "avenue": "coinbase",
            "net_pnl_usd": -5.0,
            "logged_at": now - 7200,
        },
        {
            "avenue": "kalshi",
            "net_pnl_usd": 2.0,
            "logged_at": now - 4000,
        },
    ]
    ap = compute_avenue_performance(trades, now_ts=now)
    assert "coinbase" in ap["avenues"]
    assert ap["avenues"]["coinbase"]["trade_count"] == 2
    assert ap["data_sufficiency"]["label"] in ("adequate", "thin")


def test_allocator_and_scaling_no_crash():
    ap = compute_avenue_performance([], now_ts=time.time())
    ss = {"data_quality": {"trade_rows": 0}, "weekly_pnl": 0.0, "max_drawdown": 0.0, "win_rate": None}
    ca = optimize_capital_allocation(ss, ap)
    assert "allocation_map" in ca
    sc = generate_scaling_signal(ss, {"avenue_performance": ap, "capital_allocation": ca})
    assert sc["scale_action"] in ("increase", "hold", "decrease")


def test_review_schema_ei_fields():
    d = {
        "packet_id": "p1",
        "review_type": "morning",
        "what_is_working": ["a"],
        "what_is_not_working": ["b"],
        "biggest_risk_now": "x",
        "most_fragile_part_of_system": "y",
        "best_safe_improvement": "z",
        "worst_live_behavior_to_cut": "w",
        "best_shadow_candidate_to_watch": "q",
        "capital_preservation_note": "c",
        "path_to_first_million_note": "p",
        "risk_mode_recommendation": "caution",
        "confidence_score": 0.5,
    }
    ok, errs = validate_claude_output(d, packet_id="p1", review_type="morning")
    assert ok, errs
    assert d["avenue_actions"] == []


def test_packet_contains_execution_intelligence(tmp_path, monkeypatch):
    from trading_ai.global_layer.ai_review_packet_builder import build_review_packet
    from trading_ai.global_layer.review_storage import ReviewStorage

    def fake_internal():
        return {
            "trades": [
                {
                    "avenue": "coinbase",
                    "net_pnl_usd": 1.0,
                    "logged_at": time.time(),
                }
            ],
            "trade_truth_meta": {},
            "avenue_fairness": {},
            "capital_ledger": {"net_equity_estimate_usd": 1000, "starting_capital_usd": 1000},
        }

    def fake_snap(*_a, **_k):
        return {
            "truth_version": "global_execution_intelligence_v1",
            "honesty": "test_stub",
            "avenue_performance": {
                "avenues": {},
                "strongest_avenue": "coinbase",
                "weakest_avenue": "coinbase",
                "data_sufficiency": {"label": "adequate", "notes": []},
            },
            "capital_allocation": {"allocation_map": {"coinbase": 1.0}, "reasoning": [], "risk_flags": []},
            "scaling": {"scale_action": "hold", "scale_factor": 1.0, "confidence": 0.4, "reason": "test"},
            "strategy_state": {"strategies": []},
            "goals": {"goal_id": "GOAL_A", "recommended_next_steps_today": [], "recommended_next_steps_tomorrow": []},
            "daily_progression_plan": {},
            "data_sufficiency": {"label": "adequate", "notes": []},
            "system_state_slice": {},
            "execution_intelligence_compact": {},
        }

    monkeypatch.setattr(
        "trading_ai.global_layer.ai_review_packet_builder.read_normalized_internal",
        fake_internal,
    )
    monkeypatch.setattr(
        "trading_ai.intelligence.global_execution_intelligence.build_global_execution_intelligence_snapshot",
        fake_snap,
    )
    monkeypatch.setattr(
        "trading_ai.intelligence.global_execution_intelligence.persist_execution_intelligence_artifacts",
        lambda *a, **k: None,
    )
    st = ReviewStorage(store=__import__("trading_ai.global_layer.global_memory_store", fromlist=["GlobalMemoryStore"]).GlobalMemoryStore(root=tmp_path / "gmem"))
    pkt = build_review_packet(review_type="morning", storage=st)
    assert "execution_intelligence" in pkt
    ei = pkt["execution_intelligence"]
    assert ei.get("scaling", {}).get("scale_action") == "hold"


def test_claude_runner_stub_has_ei_lists():
    from trading_ai.global_layer.claude_review_runner import run_claude_review
    from trading_ai.global_layer.review_storage import ReviewStorage
    import tempfile
    from pathlib import Path

    root = Path(tempfile.mkdtemp())
    st = ReviewStorage(store=__import__(
        "trading_ai.global_layer.global_memory_store", fromlist=["GlobalMemoryStore"]
    ).GlobalMemoryStore(root=root / "g"))
    pkt = {
        "packet_id": "test_pkt",
        "review_type": "morning",
        "execution_intelligence": {"compact": {}, "honesty": "test"},
    }
    out = run_claude_review(pkt, storage=st, force_stub=True)
    assert out.get("avenue_actions") == []
