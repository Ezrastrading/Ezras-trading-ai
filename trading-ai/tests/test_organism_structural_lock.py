"""Structural lock pass: runtime root, trade federation, governance gate, queue scoring."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_ai.automation.risk_bucket import runtime_root as risk_runtime_root
from trading_ai.global_layer.governance_order_gate import check_new_order_allowed
from trading_ai.global_layer.queue_priority_refresh import refresh_queue_priorities, score_candidate_item
from trading_ai.global_layer.review_storage import ReviewStorage
from trading_ai.global_layer.trade_truth import load_federated_trades
from trading_ai.governance.storage_architecture import runtime_root as gov_runtime_root, shark_data_dir
from trading_ai.runtime_paths import ezras_runtime_root


def test_canonical_runtime_root_matches_across_modules(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    a = ezras_runtime_root()
    b = risk_runtime_root()
    c = gov_runtime_root()
    assert a == b == c == tmp_path.resolve()
    assert shark_data_dir() == tmp_path / "shark"


def test_trade_truth_federation_meta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.nte.memory.store import MemoryStore

    ms = MemoryStore()
    ms.ensure_defaults()
    tm = ms.load_json("trade_memory.json")
    tm["trades"] = [
        {
            "trade_id": "mem1",
            "avenue": "coinbase",
            "net_pnl_usd": 1.0,
            "logged_at": "2026-04-18T12:00:00+00:00",
        }
    ]
    ms.save_json("trade_memory.json", tm)

    trades, meta = load_federated_trades(nte_store=ms)
    assert meta["model"] == "federated_nte_memory_plus_databank"
    assert len(trades) >= 1
    assert meta["nte_memory_trade_count"] == 1


def test_governance_order_gate_advisory_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOVERNANCE_ORDER_ENFORCEMENT", raising=False)
    ok, reason = check_new_order_allowed(venue="kalshi")
    assert ok is True
    assert "advisory" in reason or "disabled" in reason


def test_governance_order_gate_blocks_when_paused_and_enforced(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GOVERNANCE_ORDER_ENFORCEMENT", "true")
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    gdir = tmp_path / "shark" / "memory" / "global"
    gdir.mkdir(parents=True)
    (gdir / "joint_review_latest.json").write_text(
        json.dumps(
            {
                "joint_review_id": "jr_test",
                "live_mode_recommendation": "paused",
                "review_integrity_state": "full",
                "generated_at": "2026-04-18T12:00:00+00:00",
                "empty": False,
            }
        ),
        encoding="utf-8",
    )
    ok, reason = check_new_order_allowed(venue="coinbase")
    assert ok is False
    assert reason == "joint_review_paused"


def test_queue_refresh_adds_governance_scores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    root = tmp_path / "shark" / "memory" / "global"
    root.mkdir(parents=True)
    from trading_ai.global_layer.global_memory_store import GlobalMemoryStore

    st = ReviewStorage(store=GlobalMemoryStore(root=root))
    st.ensure_review_files()
    cq = st.load_json("candidate_queue.json")
    cq["items"] = [{"id": "c1", "post_fee_expectancy_score": 80.0}]
    st.save_json("candidate_queue.json", cq)
    refresh_queue_priorities(st)
    cq2 = st.load_json("candidate_queue.json")
    assert cq2["items"][0].get("governance_priority_score") is not None


def test_candidate_score_deterministic() -> None:
    s1 = score_candidate_item({"post_fee_expectancy_score": 50.0})
    s2 = score_candidate_item({"post_fee_expectancy_score": 50.0})
    assert s1 == s2
