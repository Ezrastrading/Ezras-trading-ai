"""Truth contract, resolved trades, EIE idempotency, autonomous domain groups."""

from __future__ import annotations

import time

import pytest

from trading_ai.intelligence.resolved_trades import resolve_for_review, resolve_for_runtime
from trading_ai.intelligence.truth_contract import summarize_policies
from trading_ai.intelligence.execution_intelligence.persistence import refresh_execution_intelligence
from trading_ai.nte.memory.store import MemoryStore
from trading_ai.orchestration.autonomous_blocker_normalization import normalize_autonomous_blockers


def test_summarize_policies_has_all_scopes():
    p = summarize_policies()
    assert "review_truth" in p and "runtime_truth" in p and "goal_truth" in p


def test_runtime_vs_review_resolution_meta(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    ms = MemoryStore()
    ms.ensure_defaults()
    tm = ms.load_json("trade_memory.json")
    now = time.time()
    tm["trades"] = [
        {
            "trade_id": "x1",
            "net_pnl_usd": 1.0,
            "logged_at": now,
            "avenue": "coinbase",
        }
    ]
    ms.save_json("trade_memory.json", tm)
    rt = resolve_for_runtime(ms)
    assert rt["source_policy_used"]["scope"] == "runtime_truth"
    rr = resolve_for_review(ms)
    assert rr["source_policy_used"]["scope"] == "review_truth"


def test_eie_idempotent_same_trade_id(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    ms = MemoryStore()
    ms.ensure_defaults()
    a = refresh_execution_intelligence(ms, persist=True, closed_trade_id="tid-1")
    b = refresh_execution_intelligence(ms, persist=True, closed_trade_id="tid-1")
    assert a.get("active_goal") == b.get("active_goal")


def test_autonomous_domain_groups_v2_present():
    norm = normalize_autonomous_blockers(
        raw_blocker_inputs=["authoritative_global_halt_blocks_autonomous", "lock_exclusivity_not_runtime_verified"],
        runtime_consistency_green=False,
    )
    g = norm.get("operator_domain_groups_v2") or {}
    assert isinstance(g, dict)
    assert any(g.values())


def test_normalize_flags_stale_halt():
    norm = normalize_autonomous_blockers(
        raw_blocker_inputs=[
            "stale_global_halt_classification_autonomous_forbidden",
            "authoritative_global_halt_blocks_autonomous",
        ],
        runtime_consistency_green=False,
    )
    ab = norm.get("active_blockers") or []
    assert "stale_global_halt_classification_autonomous_forbidden" in ab or any(
        "stale" in str(x).lower() for x in ab
    )
