import pytest

from trading_ai.global_layer.ai_review_packet_builder import (
    build_review_packet,
    build_route_summary_from_trades,
    persist_packet,
    route_bucket_for_trade,
    scheduler_gates_snapshot,
)
from trading_ai.nte.memory.store import MemoryStore
from trading_ai.global_layer.review_context_ranker import rank_packet_sections
from trading_ai.global_layer.claude_review_runner import run_claude_review
from trading_ai.global_layer.gpt_review_runner import run_gpt_review
from trading_ai.global_layer.joint_review_merger import merge_reviews
from trading_ai.global_layer.review_action_router import route_safe_actions, validate_action
from trading_ai.global_layer.review_confidence import compute_joint_confidence
from trading_ai.global_layer.review_integrity import ReviewIntegrityState
from trading_ai.global_layer.review_policy import FORBIDDEN_ACTION_TYPES
from trading_ai.global_layer.review_schema import validate_claude_output, validate_gpt_output
from trading_ai.global_layer.review_scheduler import run_full_review_cycle
from trading_ai.global_layer.review_storage import ReviewStorage


def test_rank_packet_sections_hard_stop_from_live_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    raw = {
        "risk_summary": {"loss_cluster_count": 0, "write_verification_failures": 0},
        "live_trading_summary": {"hard_stop_events": 1},
        "goal_state": {},
        "shadow_exploration_summary": {},
    }
    r = rank_packet_sections(raw)
    assert "hard_stop_events>0" in (r.get("highest_priority_anomalies") or [])


def test_scheduler_gates_snapshot_fresh_not_stale_packet(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    st = ReviewStorage()
    from trading_ai.nte.memory.store import MemoryStore

    ms = MemoryStore()
    ms.append_trade({"trade_id": "t1", "net_pnl_usd": 1.0, "avenue": "coinbase"})
    snap = scheduler_gates_snapshot(storage=st)
    assert snap["closed_trades_count"] >= 1


def test_whitelist_strips_extra_model_keys():
    from trading_ai.global_layer.review_schema import CLAUDE_OUTPUT_KEYS, whitelist_model_output

    d = {"review_id": "x", "packet_id": "p", "confidence_score": 0.5, "extra_llm_junk": "no"}
    w = whitelist_model_output(d, CLAUDE_OUTPUT_KEYS)
    assert "extra_llm_junk" not in w
    assert w.get("review_id") == "x"


def test_packet_builder_minimal(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    st = ReviewStorage()
    p = build_review_packet(review_type="morning", storage=st)
    assert p["packet_id"]
    assert "capital_state" in p
    assert "route_summary" in p
    rs = p["route_summary"]
    assert rs.get("schema_version") == "2.0"
    assert "buckets" in rs
    assert "route_a" not in rs and "route_b" not in rs
    assert "review_context_rank" in p
    persist_packet(p, storage=st)
    assert st.load_json("review_packet_latest.json").get("packet_id")


def test_route_bucket_for_trade_is_opaque_metadata_not_fixed_ab():
    from trading_ai.global_layer.ai_review_packet_builder import _UNGROUPED_BUCKET

    assert route_bucket_for_trade({"route_bucket": "Foo Bar"}) == "foo_bar"
    assert route_bucket_for_trade({"setup_type": "mean_reversion"}) == _UNGROUPED_BUCKET
    assert route_bucket_for_trade({"strategy_class": "X", "setup_type": "ignored"}) == _UNGROUPED_BUCKET
    assert route_bucket_for_trade({"route_label": "Path-1"}) == "path-1"


def test_route_summary_many_buckets_merges_tail(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    st = ReviewStorage()
    ms = MemoryStore()
    for i in range(14):
        ms.append_trade(
            {
                "trade_id": f"merge_{i}",
                "net_pnl_usd": 0.1,
                "avenue": "coinbase",
                "route_bucket": f"style_{i}",
            }
        )
    p = build_review_packet(storage=st)
    rs = p["route_summary"]
    assert rs.get("schema_version") == "2.0"
    assert "_other_merged" in rs["buckets"]
    assert rs.get("merge_note")


def test_build_route_summary_from_trades_direct():
    from trading_ai.global_layer.ai_review_packet_builder import _UNGROUPED_BUCKET

    trades = [
        {"trade_id": "a", "net_pnl_usd": 1.0, "route_bucket": "primary"},
        {"trade_id": "b", "net_pnl_usd": -1.0, "setup_type": "legacy_name"},
    ]
    out = build_route_summary_from_trades(trades)
    assert out["buckets"]["primary"]["count"] == 1
    assert out["buckets"][_UNGROUPED_BUCKET]["count"] == 1


def test_claude_gpt_stub_merge(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    st = ReviewStorage()
    p = build_review_packet(storage=st)
    cl = run_claude_review(p, storage=st, force_stub=True)
    gp = run_gpt_review(p, storage=st, force_stub=True)
    assert cl.get("stub") is True
    assert gp.get("stub") is True
    j = merge_reviews(p, cl, gp, storage=st)
    assert j.get("joint_review_id")
    assert j.get("review_integrity_state") == "full"
    assert "house_view" in j


def test_action_router_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    st = ReviewStorage()
    j = {
        "joint_review_id": "jr_test",
        "packet_id": "rp_test",
        "review_integrity_state": "full",
        "ceo_summary": "test",
        "path_to_first_million_summary": "compound",
        "changes_recommended": ["tighten_spread_filter"],
        "changes_blocked": [],
        "house_view": {"top_risk_issues": []},
        "live_mode_recommendation": "caution",
        "confidence_score": 0.5,
    }
    route_safe_actions(j, storage=st)
    cq = st.load_json("ceo_review_queue.json")
    assert len(cq.get("items") or []) >= 1


def test_forbidden_actions_blocked():
    for a in FORBIDDEN_ACTION_TYPES:
        assert validate_action(a) is False


def test_full_cycle_stub(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    st = ReviewStorage()
    out = run_full_review_cycle("morning", storage=st, skip_models=True)
    assert "joint" in out
    assert out["joint"].get("joint_review_id")
    assert out["joint"].get("review_integrity_state") == "full"


def test_schema_validation_claude_gpt():
    pid, rt = "rp_x", "morning"
    cl = {
        "review_id": "c1",
        "packet_id": pid,
        "review_type": rt,
        "what_is_working": ["a"],
        "what_is_not_working": ["b"],
        "biggest_risk_now": "r",
        "most_fragile_part_of_system": "f",
        "best_safe_improvement": "i",
        "worst_live_behavior_to_cut": "x",
        "best_shadow_candidate_to_watch": "s",
        "capital_preservation_note": "n",
        "path_to_first_million_note": "p",
        "risk_mode_recommendation": "normal",
        "confidence_score": 0.8,
    }
    ok, _ = validate_claude_output(cl, packet_id=pid, review_type=rt)
    assert ok
    gp = {
        "review_id": "g1",
        "packet_id": pid,
        "review_type": rt,
        "top_3_decisions": ["d"],
        "top_3_warnings": ["w"],
        "top_3_next_actions": ["n"],
        "live_status_recommendation": "normal",
        "best_live_edge_now": "e",
        "weakest_live_edge_now": "we",
        "best_growth_opportunity": "g",
        "main_bottleneck_to_first_million": "b",
        "short_ceo_note": "ceo",
        "confidence_score": 0.7,
    }
    ok2, _ = validate_gpt_output(gp, packet_id=pid, review_type=rt)
    assert ok2


def test_merge_pause_vs_normal_packet_risky(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    st = ReviewStorage()
    p = build_review_packet(storage=st)
    p["risk_summary"]["write_verification_failures"] = 1
    cl = {
        "review_id": "c1",
        "packet_id": p["packet_id"],
        "review_type": p["review_type"],
        "what_is_working": [],
        "what_is_not_working": [],
        "biggest_risk_now": "x",
        "most_fragile_part_of_system": "y",
        "best_safe_improvement": "z",
        "worst_live_behavior_to_cut": "a",
        "best_shadow_candidate_to_watch": "b",
        "capital_preservation_note": "n",
        "path_to_first_million_note": "p",
        "risk_mode_recommendation": "paused",
        "confidence_score": 0.5,
        "_validation_ok": True,
    }
    gp = {
        "review_id": "g1",
        "packet_id": p["packet_id"],
        "review_type": p["review_type"],
        "top_3_decisions": ["d"],
        "top_3_warnings": ["w"],
        "top_3_next_actions": ["n"],
        "live_status_recommendation": "normal",
        "best_live_edge_now": "e",
        "weakest_live_edge_now": "we",
        "best_growth_opportunity": "g",
        "main_bottleneck_to_first_million": "b",
        "short_ceo_note": "ceo",
        "confidence_score": 0.9,
        "_validation_ok": True,
    }
    j = merge_reviews(p, cl, gp, storage=st)
    assert j["live_mode_recommendation"] == "paused"


def test_joint_confidence_degraded_cap():
    jc = compute_joint_confidence(
        claude_confidence_01=0.9,
        gpt_confidence_01=0.9,
        packet_completeness_0_100=90.0,
        agreement_score_0_100=80.0,
        anomaly_aggregate_0_100=10.0,
        sample_strength_0_100=80.0,
        review_integrity=ReviewIntegrityState.DEGRADED,
        live_mode_disagreement=False,
        anomaly_aggregate_for_cap=10.0,
    )
    assert jc <= 0.74


def test_joint_confidence_failed_zero():
    z = compute_joint_confidence(
        claude_confidence_01=0.9,
        gpt_confidence_01=0.9,
        packet_completeness_0_100=90.0,
        agreement_score_0_100=80.0,
        anomaly_aggregate_0_100=0.0,
        sample_strength_0_100=80.0,
        review_integrity=ReviewIntegrityState.FAILED,
        live_mode_disagreement=False,
        anomaly_aggregate_for_cap=0.0,
    )
    assert z == 0.0
