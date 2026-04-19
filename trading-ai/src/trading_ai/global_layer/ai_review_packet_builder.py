"""Build compressed ``review_packet_latest.json`` for all avenues — evidence-dense, low token cost."""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from trading_ai.global_layer.internal_data_reader import read_normalized_internal
from trading_ai.global_layer.pnl_aggregator import aggregate_from_trades
from trading_ai.global_layer.review_context_ranker import rank_packet_sections, trim_packet_for_budget
from trading_ai.global_layer.review_policy import ReviewPolicy, load_policy_from_environ
from trading_ai.global_layer.review_storage import ReviewStorage

FIRST_MILLION_USD = 1_000_000.0


def _strategy_route_label(setup: Optional[str]) -> str:
    s = (setup or "").lower()
    if "mean_reversion" in s or s == "a":
        return "route_a"
    if "continuation" in s or "pullback" in s or s == "b":
        return "route_b"
    return "other"


def _iso_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _nte_execution_counters() -> Dict[str, Any]:
    try:
        from trading_ai.nte.paths import nte_memory_dir

        p = nte_memory_dir() / "execution_counters.json"
        if p.is_file():
            raw = json.loads(p.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
    except Exception:
        pass
    return {}


def _nte_consecutive_losses() -> int:
    try:
        from trading_ai.nte.execution.state import load_state

        return int((load_state() or {}).get("consecutive_losses") or 0)
    except Exception:
        return 0


def _route_stats(trades: List[Dict[str, Any]], label: str) -> Dict[str, Any]:
    subs = [t for t in trades if _strategy_route_label(str(t.get("setup_type") or "")) == label]
    c = len(subs)
    if c == 0:
        return {
            "count": 0,
            "net_pnl_usd": 0.0,
            "win_rate": 0.0,
            "trade_quality_avg": 0.0,
            "expected_edge_bps_avg": 0.0,
            "realized_move_bps_avg": 0.0,
            "avg_hold_sec": 0.0,
            "avg_slippage_bps": 0.0,
        }
    wins = sum(1 for t in subs if float(t.get("net_pnl_usd") or 0) > 0)
    exp_sum = sum(float(t.get("expected_edge_bps") or 0) for t in subs)
    rm_sum = sum(float(t.get("realized_move_bps") or 0) for t in subs if t.get("realized_move_bps") is not None)
    rm_n = sum(1 for t in subs if t.get("realized_move_bps") is not None)
    hold_sum = sum(float(t.get("duration_sec") or 0) for t in subs)
    return {
        "count": c,
        "net_pnl_usd": sum(float(t.get("net_pnl_usd") or 0) for t in subs),
        "win_rate": wins / float(c),
        "trade_quality_avg": 0.0,
        "expected_edge_bps_avg": exp_sum / c,
        "realized_move_bps_avg": (rm_sum / rm_n) if rm_n else 0.0,
        "avg_hold_sec": hold_sum / c,
        "avg_slippage_bps": 0.0,
    }


def build_review_packet(
    *,
    review_type: str = "morning",
    storage: Optional[ReviewStorage] = None,
    policy: Optional[ReviewPolicy] = None,
) -> Dict[str, Any]:
    """
    Assemble review packet from global + NTE signals (all avenues).

    ``review_type``: morning | midday | eod | exception
    """
    policy = policy or load_policy_from_environ()
    st = storage or ReviewStorage()
    st.ensure_review_files()

    internal = read_normalized_internal()
    trades: List[Dict[str, Any]] = [t for t in (internal.get("trades") or []) if isinstance(t, dict)]
    agg = aggregate_from_trades(trades)

    led = internal.get("capital_ledger") or {}
    equity = float(led.get("net_equity_estimate_usd") or 0.0)
    start = float(led.get("starting_capital_usd") or 0.0)
    progress_pct = min(100.0, max(0.0, (equity / FIRST_MILLION_USD) * 100.0)) if FIRST_MILLION_USD > 0 else 0.0

    gstore = st.store
    sp = gstore.load_json("speed_progression.json")
    weekly_net = float((gstore.load_json("weekly_pnl_summary.json") or {}).get("period_net_usd") or 0.0)
    daily_net = float((gstore.load_json("daily_pnl_summary.json") or {}).get("period_net_usd") or 0.0)

    # Avenue rollup
    by_av = agg.get("by_avenue") or {}
    avenue_summary: List[Dict[str, Any]] = []
    labels = ["A", "B", "C"]
    for i, (aid, net) in enumerate(sorted(by_av.items(), key=lambda x: -abs(x[1]))[:5]):
        sub = [t for t in trades if str(t.get("avenue") or t.get("avenue_id") or "") == aid]
        wins = sum(1 for t in sub if float(t.get("net_pnl_usd") or 0) > 0)
        avenue_summary.append(
            {
                "avenue_id": labels[i] if i < 3 else aid,
                "name": aid,
                "net_pnl_usd": float(net),
                "win_rate": (wins / len(sub)) if sub else 0.0,
                "trade_quality_avg": 0.0,
                "risk_flag_count": 0,
                "live_status": "normal",
            }
        )

    best_av = max(by_av, key=by_av.get) if by_av else ""
    worst_av = min(by_av, key=by_av.get) if by_av else ""

    ra = _route_stats(trades, "route_a")
    rb = _route_stats(trades, "route_b")

    ec = _nte_execution_counters()

    # Risk / monitoring — optional NTE dashboard
    risk_summary: Dict[str, Any] = {
        "main_risk_label": "unknown",
        "max_adverse_excursion_bps": 0.0,
        "loss_cluster_count": 0,
        "write_verification_failures": 0,
        "ws_market_stale_events": 0,
        "ws_user_stale_events": 0,
        "slippage_cluster_events": 0,
        "health_degraded_events": 0,
    }
    try:
        from trading_ai.nte.monitoring.live_dashboard import build_live_monitoring_dashboard

        dash = build_live_monitoring_dashboard(engine=None, user_ws_stale=None)
        hs = dash.get("hard_stop") or {}
        if hs.get("stop_new_entries"):
            risk_summary["main_risk_label"] = "execution"
        a = dash.get("A_system_health") or {}
        if (a.get("ws_market") or {}).get("stale"):
            risk_summary["ws_market_stale_events"] = 1
        j = dash.get("J_risk_state") or {}
        if int(j.get("consecutive_losses") or 0) >= 3:
            risk_summary["loss_cluster_count"] = 1
    except Exception:
        pass

    shadow = st.load_json("candidate_queue.json")
    items = shadow.get("items") or []
    shadow_summary = {
        "shadow_candidates_count": len(items),
        "promotion_pending_count": len([x for x in items if isinstance(x, dict) and x.get("status") == "promotion_pending"]),
        "top_profit_candidates": [x for x in items[:3] if isinstance(x, dict)],
        "top_risk_reduction_candidates": [],
        "top_latency_candidates": [],
        "top_speed_to_goal_candidates": [],
    }

    rr = st.load_json("risk_reduction_queue.json")
    ri = rr.get("items") or []
    if isinstance(ri, list):
        shadow_summary["top_risk_reduction_candidates"] = ri[:3]

    packet_id = f"rp_{datetime.now(timezone.utc).strftime('%Y_%m_%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

    raw: Dict[str, Any] = {
        "packet_id": packet_id,
        "review_type": review_type,
        "generated_at": _iso_compact(),
        "engine_mode": (os.environ.get("ORG_ENGINE_MODE") or "live_locked").strip(),
        "capital_state": {
            "current_equity_usd": equity,
            "starting_equity_usd": start,
            "daily_net_pnl_usd": daily_net,
            "weekly_net_pnl_usd": weekly_net,
            "monthly_net_pnl_usd": float(agg.get("rolling_30d_net_usd") or 0.0),
            "drawdown_pct": 0.0,
            "progress_to_first_million_pct": progress_pct,
            "estimated_capital_velocity_label": str(sp.get("current_speed", {}).get("progress_rate_label") or "base"),
        },
        "avenue_state": {
            "active_avenues": list(by_av.keys())[:8],
            "best_avenue_now": best_av,
            "weakest_avenue_now": worst_av,
            "avenue_summary": avenue_summary,
        },
        "live_trading_summary": {
            "closed_trades_count": len(trades),
            "open_positions_count": 0,
            "pending_orders_count": 0,
            "win_rate": sum(1 for t in trades if float(t.get("net_pnl_usd") or 0) > 0) / max(1, len(trades)),
            "avg_net_pnl_usd": sum(float(t.get("net_pnl_usd") or 0) for t in trades) / max(1, len(trades)),
            "avg_fees_usd": sum(float(t.get("fees") or t.get("fees_usd") or 0) for t in trades) / max(1, len(trades)),
            "avg_slippage_bps": 0.0,
            "consecutive_losses": _nte_consecutive_losses(),
            "stale_cancel_count": int(ec.get("stale_pending_canceled") or 0),
            "hard_stop_events": 1 if risk_summary.get("loss_cluster_count") else 0,
            "partial_failure_events": 0,
        },
        "route_summary": {"route_a": ra, "route_b": rb},
        "risk_summary": risk_summary,
        "shadow_exploration_summary": shadow_summary,
        "goal_state": {
            "current_best_live_edge": str(sp.get("strongest_avenue") or best_av or ""),
            "current_weakest_live_edge": str(sp.get("weakest_avenue") or worst_av or ""),
            "main_bottleneck_to_first_million": str(
                (sp.get("blockers") or [{}])[0].get("explanation") if sp.get("blockers") else ""
            ),
            "main_opportunity_to_first_million": str((sp.get("best_path") or {}).get("fastest_realistic_path") or ""),
            "current_path_quality": str(sp.get("current_speed", {}).get("progress_rate_label") or "developing"),
        },
        "lesson_state": {
            "top_positive_lessons": [],
            "top_negative_lessons": [],
            "top_immediate_actions": list((sp.get("best_path") or {}).get("top_3_actions") or [])[:5],
        },
        "review_context_rank": {},
    }

    raw["review_context_rank"] = rank_packet_sections(raw)
    out, _trunc = trim_packet_for_budget(raw, max_chars=policy.max_packet_chars)
    return out


def persist_packet(packet: Dict[str, Any], *, storage: Optional[ReviewStorage] = None) -> None:
    st = storage or ReviewStorage()
    st.save_json("review_packet_latest.json", packet)
    st.append_jsonl("review_packet_history.jsonl", {"ts": time.time(), "packet_id": packet.get("packet_id"), "review_type": packet.get("review_type")})
