"""Build compressed ``review_packet_latest.json`` for all avenues — evidence-dense, low token cost."""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.global_layer.internal_data_reader import read_normalized_internal
from trading_ai.global_layer.pnl_aggregator import aggregate_from_trades
from trading_ai.global_layer.review_context_ranker import rank_packet_sections, trim_packet_for_budget
from trading_ai.global_layer.review_policy import ReviewPolicy, load_policy_from_environ
from trading_ai.global_layer.review_storage import ReviewStorage
from trading_ai.nte.memory.store import MemoryStore

FIRST_MILLION_USD = 1_000_000.0

# Trades without an explicit operator routing label are grouped here — never from strategy/setup inference.
_UNGROUPED_BUCKET = "_ungrouped"

_BUCKET_SLUG_RE = re.compile(r"[^a-z0-9_.-]+")


def _slug_bucket(s: str) -> str:
    s = (s or "").strip().lower()
    if not s:
        return "unknown"
    s = _BUCKET_SLUG_RE.sub("_", s).strip("_")[:64]
    return s or "unknown"


def route_bucket_for_trade(t: Dict[str, Any]) -> str:
    """
    Opaque bucket id for packet rollups — **explicit routing metadata only**.

    ``strategy_class``, ``strategy_family``, ``setup_type``, etc. are passed through on trade
    rows as optional metadata; they MUST NOT affect grouping or any downstream organism decision.
    Priority: ``route_bucket`` / ``router_bucket`` → ``route_label`` (neutral pathway tag) →
    :data:`_UNGROUPED_BUCKET` when nothing explicit is set.
    """
    for key in ("route_bucket", "router_bucket"):
        v = t.get(key)
        if v is not None and str(v).strip():
            return _slug_bucket(str(v))
    v = t.get("route_label")
    if v is not None and str(v).strip():
        return _slug_bucket(str(v))
    return _UNGROUPED_BUCKET


def _bucket_choice_source(t: Dict[str, Any]) -> str:
    if t.get("route_bucket") or t.get("router_bucket"):
        return "explicit_route_bucket"
    if t.get("route_label"):
        return "route_label_only"
    return "ungrouped_no_explicit_route_metadata"


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


def _nte_open_pending_counts() -> Tuple[int, int, bool]:
    """Coinbase NTE engine state: open positions + pending entry orders (if state file readable)."""
    try:
        from trading_ai.nte.execution.state import load_state

        st = load_state()
        pos = st.get("positions") or []
        pend = st.get("pending_entry_orders") or []
        if not isinstance(pos, list):
            pos = []
        if not isinstance(pend, list):
            pend = []
        return len(pos), len(pend), True
    except Exception:
        return 0, 0, False


def _coarse_max_anomaly_severity(risk_summary: Dict[str, Any], live_summary: Dict[str, Any]) -> float:
    """Bounded 0–100 severity hint for merger/confidence — conservative, not venue truth."""
    rs = risk_summary or {}
    lt = live_summary or {}
    sev = 0.0
    if int(rs.get("write_verification_failures") or 0) > 0:
        sev = max(sev, 70.0)
    if int(rs.get("loss_cluster_count") or 0) > 0:
        sev = max(sev, 55.0)
    if int(lt.get("hard_stop_events") or 0) > 0:
        sev = max(sev, 60.0)
    if int(rs.get("ws_market_stale_events") or 0) > 0 or int(rs.get("ws_user_stale_events") or 0) > 0:
        sev = max(sev, 40.0)
    return min(100.0, sev)


def _net_for_trade(t: Dict[str, Any]) -> Optional[float]:
    v = t.get("net_pnl_usd")
    if v is None:
        v = t.get("net_pnl")
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _win_rate_known_net(trades: List[Dict[str, Any]]) -> float:
    nets = [_net_for_trade(t) for t in trades]
    known = [n for n in nets if n is not None]
    if not known:
        return 0.0
    wins = sum(1 for n in known if n > 0)
    return wins / float(len(known))


def _avg_slippage_bps_for_packet(trades: List[Dict[str, Any]]) -> Tuple[Optional[float], str]:
    vals: List[float] = []
    for t in trades:
        for k in ("entry_slippage_bps", "exit_slippage_bps"):
            x = t.get(k)
            if x is not None:
                try:
                    vals.append(abs(float(x)))
                except (TypeError, ValueError):
                    pass
    if not trades:
        return None, "no_trades"
    if not vals:
        return None, "missing_or_thin"
    return sum(vals) / len(vals), "estimated_from_trade_rows"


def _field_quality_summary(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {
            "net_pnl_coverage_label": "none",
            "slippage_coverage_label": "none",
            "trades_with_known_net": 0,
            "trades_with_any_slippage_field": 0,
        }
    nk = sum(1 for t in trades if _net_for_trade(t) is not None)
    ns = sum(
        1
        for t in trades
        if any(t.get(k) is not None for k in ("entry_slippage_bps", "exit_slippage_bps", "realized_move_bps"))
    )
    net_label = "full" if nk == n else ("partial_unknown_net" if nk > 0 else "none")
    slip_label = "good" if ns >= max(1, n // 2) else ("partial" if ns else "missing_or_thin")
    return {
        "net_pnl_coverage_label": net_label,
        "slippage_coverage_label": slip_label,
        "trades_with_known_net": nk,
        "trades_with_any_slippage_field": ns,
    }


def _route_stats_for_group(subs: List[Dict[str, Any]]) -> Dict[str, Any]:
    c = len(subs)
    if c == 0:
        return {
            "count": 0,
            "net_pnl_usd": 0.0,
            "win_rate": 0.0,
            "trade_quality_avg": 0.0,
            "expected_edge_bps_avg": None,
            "expected_edge_bps_avg_note": "no_trades",
            "realized_move_bps_avg": None,
            "realized_move_bps_avg_note": "no_trades",
            "avg_hold_sec": 0.0,
            "avg_slippage_bps": None,
            "avg_slippage_bps_note": "no_trades",
        }
    wins = sum(1 for t in subs if (_net_for_trade(t) or 0) > 0)
    exp_vals: List[float] = []
    for t in subs:
        x = t.get("expected_edge_bps")
        if x is None:
            x = t.get("expected_net_edge_bps")
        if x is not None:
            try:
                exp_vals.append(float(x))
            except (TypeError, ValueError):
                pass
    rm_sum = sum(float(t.get("realized_move_bps") or 0) for t in subs if t.get("realized_move_bps") is not None)
    rm_n = sum(1 for t in subs if t.get("realized_move_bps") is not None)
    hold_vals = []
    for t in subs:
        d = t.get("duration_sec")
        if d is not None:
            try:
                hold_vals.append(float(d))
            except (TypeError, ValueError):
                pass
    slip_vals: List[float] = []
    for t in subs:
        for k in ("entry_slippage_bps", "exit_slippage_bps"):
            x = t.get(k)
            if x is not None:
                try:
                    slip_vals.append(abs(float(x)))
                except (TypeError, ValueError):
                    pass
    return {
        "count": c,
        "net_pnl_usd": sum(_net_for_trade(t) or 0.0 for t in subs),
        "win_rate": wins / float(c),
        "trade_quality_avg": 0.0,
        "expected_edge_bps_avg": (sum(exp_vals) / len(exp_vals)) if exp_vals else None,
        "expected_edge_bps_avg_note": "known_rows_only" if exp_vals else "missing_or_unknown",
        "realized_move_bps_avg": (rm_sum / rm_n) if rm_n else None,
        "realized_move_bps_avg_note": "known_rows_only" if rm_n else "missing_or_unknown",
        "avg_hold_sec": (sum(hold_vals) / len(hold_vals)) if hold_vals else None,
        "avg_slippage_bps": (sum(slip_vals) / len(slip_vals)) if slip_vals else None,
        "avg_slippage_bps_note": "known_rows_only" if slip_vals else "missing_or_unknown",
    }


def build_route_summary_from_trades(
    trades: List[Dict[str, Any]],
    *,
    max_buckets: int = 12,
) -> Dict[str, Any]:
    """
    Neutral per-bucket stats for the review packet. Bucket ids are opaque labels, not organism routing truth.
    If there are more than ``max_buckets`` distinct buckets, the smallest groups are merged into ``_other_merged``.
    """
    groups: Dict[str, List[Dict[str, Any]]] = {}
    source_counts: Dict[str, int] = {
        "explicit_route_bucket": 0,
        "route_label_only": 0,
        "ungrouped_no_explicit_route_metadata": 0,
    }
    for t in trades:
        b = route_bucket_for_trade(t)
        groups.setdefault(b, []).append(t)
        src = _bucket_choice_source(t)
        source_counts[src] = source_counts.get(src, 0) + 1

    order = sorted(groups.keys(), key=lambda k: -len(groups[k]))
    merge_note: Optional[str] = None
    if len(order) > max_buckets - 1:
        keep_keys = order[: max_buckets - 1]
        tail: List[Dict[str, Any]] = []
        for k in order[max_buckets - 1 :]:
            tail.extend(groups[k])
        out_groups: Dict[str, List[Dict[str, Any]]] = {k: groups[k] for k in keep_keys}
        if tail:
            out_groups["_other_merged"] = tail
            merge_note = f"merged_tail_{len(order) - max_buckets + 1}_buckets"
        groups = out_groups
    else:
        groups = {k: groups[k] for k in order}

    buckets_payload = {bid: _route_stats_for_group(glist) for bid, glist in groups.items()}
    return {
        "schema_version": "2.0",
        "buckets": buckets_payload,
        "bucket_order": list(buckets_payload.keys()),
        "bucket_choice_source_counts": source_counts,
        "merge_note": merge_note,
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
    ei_snapshot: Dict[str, Any] = {}
    try:
        from trading_ai.intelligence.global_execution_intelligence import (
            build_global_execution_intelligence_snapshot,
            persist_execution_intelligence_artifacts,
        )

        ei_snapshot = build_global_execution_intelligence_snapshot(now_ts=time.time(), nte_store=MemoryStore())
        rr: Optional[str] = None
        try:
            from trading_ai.runtime_paths import ezras_runtime_root

            rr = str(ezras_runtime_root())
        except Exception:
            rr = None
        persist_execution_intelligence_artifacts(ei_snapshot, review_storage=st, runtime_root=rr)
    except Exception as exc:
        ei_snapshot = {
            "truth_version": "global_execution_intelligence_v1",
            "generated_at": _iso_compact(),
            "honesty": f"snapshot_unavailable:{exc}",
            "avenue_performance": {"avenues": {}, "data_sufficiency": {"label": "missing", "notes": ["ei_build_failed"]}},
            "capital_allocation": {"allocation_map": {}, "reasoning": [], "risk_flags": ["ei_unavailable"]},
            "scaling": {"scale_action": "hold", "scale_factor": 1.0, "confidence": 0.0, "reason": "ei_unavailable"},
            "strategy_state": {"strategies": []},
            "goals": {"goal_id": "unknown", "honesty": "missing"},
            "daily_progression_plan": {},
            "data_sufficiency": {"label": "missing", "notes": ["ei_build_failed"]},
            "execution_intelligence_compact": {},
        }
    truth_meta = internal.get("trade_truth_meta") if isinstance(internal.get("trade_truth_meta"), dict) else {}
    avenue_fair = internal.get("avenue_fairness") if isinstance(internal.get("avenue_fairness"), dict) else {}
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
        wins = sum(1 for t in sub if (_net_for_trade(t) or 0) > 0)
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

    route_summary = build_route_summary_from_trades(trades)

    hard_stop_n = sum(
        1
        for t in trades
        if str(t.get("exit_reason") or "") == "stop_loss"
        or bool(t.get("hard_stop_exit"))
        or any("hard_stop" in str(x).lower() for x in (t.get("anomaly_flags") or []))
    )

    ec = _nte_execution_counters()
    open_pos_n, pend_ord_n, nte_positions_ok = _nte_open_pending_counts()
    slip_avg, slip_label = _avg_slippage_bps_for_packet(trades)
    field_q = _field_quality_summary(trades)
    readiness_caveats: List[str] = []
    if truth_meta.get("federation_conflict_count"):
        readiness_caveats.append(
            f"federation_conflicts={truth_meta.get('federation_conflict_count')} (see packet_truth)"
        )
    if field_q.get("net_pnl_coverage_label") == "partial_unknown_net":
        readiness_caveats.append("Some trades lack net PnL in federated ingest — win_rate uses known-net subset only.")
    if field_q.get("slippage_coverage_label") in ("missing_or_thin", "partial"):
        readiness_caveats.append("Slippage coverage thin — avg_slippage_bps may be null or partial.")
    if not nte_positions_ok:
        readiness_caveats.append("NTE Coinbase positions file unreadable — open/pending counts not sourced.")
    for w in truth_meta.get("warnings") or []:
        if "Kalshi" in w:
            readiness_caveats.append(str(w))

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
    if hard_stop_n:
        risk_summary["main_risk_label"] = "execution"
        risk_summary["hard_stop_exit_count"] = hard_stop_n

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
            "open_positions_count": open_pos_n,
            "pending_orders_count": pend_ord_n,
            "win_rate": _win_rate_known_net(trades),
            "win_rate_note": "computed_on_trades_with_known_net_only"
            if field_q.get("net_pnl_coverage_label") == "partial_unknown_net"
            else "all_trades_have_net",
            "avg_net_pnl_usd": sum(_net_for_trade(t) or 0.0 for t in trades) / max(1, len(trades)),
            "avg_fees_usd": sum(float(t.get("fees") or t.get("fees_usd") or t.get("fees_paid") or 0) for t in trades)
            / max(1, len(trades)),
            "avg_slippage_bps": slip_avg,
            "avg_slippage_bps_note": slip_label,
            "consecutive_losses": _nte_consecutive_losses(),
            "stale_cancel_count": int(ec.get("stale_pending_canceled") or 0),
            "hard_stop_events": hard_stop_n,
            "partial_failure_events": 0,
            "first_twenty_trades_sample_size": min(20, len(trades)),
        },
        "route_summary": route_summary,
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
        "execution_intelligence": {
            "avenues": (ei_snapshot.get("avenue_performance") or {}).get("avenues"),
            "capital_allocation": ei_snapshot.get("capital_allocation"),
            "scaling": ei_snapshot.get("scaling"),
            "strategy_state": ei_snapshot.get("strategy_state"),
            "goals": ei_snapshot.get("goals"),
            "daily_progression_plan": ei_snapshot.get("daily_progression_plan"),
            "data_sufficiency": ei_snapshot.get("data_sufficiency"),
            "compact": ei_snapshot.get("execution_intelligence_compact"),
            "honesty": ei_snapshot.get("honesty"),
            "best_next_steps_today": list((ei_snapshot.get("goals") or {}).get("recommended_next_steps_today") or [])[:12],
            "best_next_steps_tomorrow": list((ei_snapshot.get("goals") or {}).get("recommended_next_steps_tomorrow") or [])[:12],
            "strongest_avenue": (ei_snapshot.get("avenue_performance") or {}).get("strongest_avenue"),
            "weakest_avenue": (ei_snapshot.get("avenue_performance") or {}).get("weakest_avenue"),
            "biggest_blocker": list((ei_snapshot.get("goals") or {}).get("blockers") or [])[:1],
        },
        "review_context_rank": {},
        "packet_truth": {
            "model": truth_meta.get("model", "federated_nte_memory_plus_databank"),
            "precedence": truth_meta.get(
                "precedence",
                "nte_trade_memory_primary_databank_enrichment_and_orphan_rows",
            ),
            "nte_memory_trade_count": truth_meta.get("nte_memory_trade_count"),
            "databank_event_count": truth_meta.get("databank_event_count"),
            "databank_root": truth_meta.get("databank_root"),
            "databank_root_source": truth_meta.get("databank_root_source"),
            "merged_trade_count": truth_meta.get("merged_trade_count"),
            "databank_only_trade_count": truth_meta.get("databank_only_trade_count"),
            "databank_duplicate_trade_id_count": truth_meta.get("databank_duplicate_trade_id_count"),
            "federation_conflict_count": truth_meta.get("federation_conflict_count"),
            "fairness_warnings": truth_meta.get("fairness_warnings")
            or truth_meta.get("warnings")
            or [],
            "avenue_fairness": avenue_fair.get("by_avenue") or {},
            "avenue_representation": truth_meta.get("avenue_representation") or {},
            "expected_avenues": truth_meta.get("expected_avenues") or [],
            "field_quality_summary": field_q,
            "readiness_caveats": readiness_caveats,
            "play_money_labeled": bool(truth_meta.get("play_money_labeled")),
            "open_positions_reported": bool(nte_positions_ok),
            "aggregate_slippage_in_packet": slip_avg is not None,
            "limitations": [
                "Federation: see trade_truth module — memory wins numeric conflicts; enrichment fills nulls only.",
                "Open/pending counts are sourced from NTE Coinbase engine state when the state file is readable; "
                "otherwise counts are zero with open_positions_reported=false (not fake certainty).",
                "avg_slippage_bps is null when no per-trade slippage fields are present — not treated as zero edge.",
                "route_summary.buckets use explicit route_bucket/router_bucket/route_label only; "
                "strategy_class/setup_type are not interpreted for grouping (schema v2).",
                "execution_intelligence is derived from this packet's federated trades plus NTE strategy_scores/goals; "
                "capital allocation and scaling signals are advisory and do not move funds or change live permissions.",
            ],
        },
    }

    try:
        from trading_ai.global_layer.bot_hierarchy.integration import (
            build_execution_intelligence_hierarchy_advisory,
            build_review_packet_hierarchy_section,
        )

        raw["bot_hierarchy_snapshot"] = build_review_packet_hierarchy_section()
        adv = build_execution_intelligence_hierarchy_advisory()
        if isinstance(raw.get("execution_intelligence"), dict):
            raw["execution_intelligence"]["hierarchy_advisory_context"] = adv
    except Exception as exc:
        raw["bot_hierarchy_snapshot"] = {"truth_version": "bot_hierarchy_review_section_v1", "honesty": f"unavailable:{exc}"}

    # Single place for downstream ranker/merger: mirror hard-stop into risk_summary when live summary flags it.
    lt0 = raw["live_trading_summary"]
    raw["risk_summary"]["hard_stop_events"] = int(lt0.get("hard_stop_events") or 0)
    raw["risk_summary"]["max_anomaly_severity"] = _coarse_max_anomaly_severity(raw["risk_summary"], raw["live_trading_summary"])

    raw["review_context_rank"] = rank_packet_sections(raw)
    out, _trunc = trim_packet_for_budget(raw, max_chars=policy.max_packet_chars)
    return out


def persist_packet(packet: Dict[str, Any], *, storage: Optional[ReviewStorage] = None) -> None:
    st = storage or ReviewStorage()
    st.save_json("review_packet_latest.json", packet)
    st.append_jsonl("review_packet_history.jsonl", {"ts": time.time(), "packet_id": packet.get("packet_id"), "review_type": packet.get("review_type")})


def scheduler_gates_snapshot(
    *,
    storage: Optional[ReviewStorage] = None,
    policy: Optional[ReviewPolicy] = None,
) -> Dict[str, Any]:
    """
    Fresh counts for ``tick_scheduler`` — does not use stale ``review_packet_latest.json``.

    Returns keys: ``closed_trades_count``, ``shadow_candidates_count``, ``anomaly_loss_cluster_flag`` (0|1).
    """
    policy = policy or load_policy_from_environ()
    st = storage or ReviewStorage()
    st.ensure_review_files()
    internal = read_normalized_internal()
    trades: List[Dict[str, Any]] = [t for t in (internal.get("trades") or []) if isinstance(t, dict)]
    closed = len(trades)
    sh = st.load_json("candidate_queue.json")
    shadow_n = len(sh.get("items") or [])
    anom = 0
    try:
        from trading_ai.nte.monitoring.live_dashboard import build_live_monitoring_dashboard

        dash = build_live_monitoring_dashboard(engine=None, user_ws_stale=None)
        j = dash.get("J_risk_state") or {}
        if int(j.get("consecutive_losses") or 0) >= 3:
            anom = 1
    except Exception:
        pass
    return {
        "closed_trades_count": closed,
        "shadow_candidates_count": shadow_n,
        "anomaly_loss_cluster_flag": anom,
        "policy_class": policy.review_token_budget_class,
    }
