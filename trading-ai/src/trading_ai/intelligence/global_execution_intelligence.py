"""Build and persist execution intelligence artifacts for global review (advisory)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from trading_ai.intelligence.avenue_performance import compute_avenue_performance, net_pnl_for_trade
from trading_ai.intelligence.capital_allocator import optimize_capital_allocation
from trading_ai.intelligence.execution_intelligence.evaluation import evaluate_goal_progress
from trading_ai.intelligence.execution_intelligence.goals import get_goal
from trading_ai.intelligence.execution_intelligence.metrics_common import max_drawdown_cumulative_pnls
from trading_ai.intelligence.execution_intelligence.system_state import weekly_net_by_avenue
from trading_ai.intelligence.ts_parse import parse_trade_ts
from trading_ai.intelligence.scaling_engine import generate_scaling_signal
from trading_ai.intelligence.strategy_manager import build_strategy_state_summary
from trading_ai.intelligence.resolved_trades import build_discrepancy_report, resolve_for_review, resolve_for_runtime
from trading_ai.intelligence.truth_contract import policy_for_goal, policy_for_review
from trading_ai.nte.capital_ledger import load_ledger, net_equity_estimate
from trading_ai.nte.memory.store import MemoryStore

logger = logging.getLogger(__name__)


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def derive_system_state_for_ei(trades: List[Dict[str, Any]], *, now_ts: float) -> Dict[str, Any]:
    """Minimal system_state slice from arbitrary federated trade rows (truth-aligned keys)."""
    import time

    now = now_ts if now_ts else time.time()
    day_start = datetime.fromtimestamp(now, tz=timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    t_day = day_start.timestamp()
    week_start = day_start - timedelta(days=day_start.weekday())
    t_week = week_start.timestamp()

    daily_pnls: List[float] = []
    weekly_pnls: List[float] = []
    all_ordered: List[tuple] = []
    trade_count_today = 0

    for t in trades:
        if not isinstance(t, dict):
            continue
        pnl = net_pnl_for_trade(t)
        if pnl is None:
            continue
        ts = parse_trade_ts(t)
        if ts is not None:
            all_ordered.append((ts, pnl))
            if ts >= t_day:
                daily_pnls.append(pnl)
                trade_count_today += 1
            if ts >= t_week:
                weekly_pnls.append(pnl)

    all_ordered.sort(key=lambda x: x[0])
    seq = [p for _, p in all_ordered]
    max_dd = max_drawdown_cumulative_pnls(seq) if seq else 0.0

    pnls = [net_pnl_for_trade(t) for t in trades if isinstance(t, dict)]
    known = [p for p in pnls if p is not None]
    wins = [p for p in known if p > 0]
    win_rate = (len(wins) / len(known)) if known else None

    trade_count_week = 0
    for t in trades:
        if not isinstance(t, dict):
            continue
        ts = parse_trade_ts(t)
        if ts is not None and ts >= t_week:
            trade_count_week += 1

    return {
        "weekly_pnl": sum(weekly_pnls),
        "daily_pnl": sum(daily_pnls),
        "max_drawdown": float(max_dd),
        "win_rate": win_rate,
        "trade_count_week": trade_count_week,
        "trade_count_today": trade_count_today,
        "data_quality": {
            "trade_rows": len(trades),
            "trades_with_known_net": len(known),
            "trades_with_parseable_ts": sum(1 for t in trades if isinstance(t, dict) and parse_trade_ts(t) is not None),
        },
        "edge_stability_score": None,
        "volatility_state": {"label": "unknown"},
        "generated_at": _iso(),
    }


def _goal_progress_block(
    store: MemoryStore,
    state: Dict[str, Any],
    trades: List[Dict[str, Any]],
    *,
    now_ts: float,
    avenue_keys: List[str],
) -> Dict[str, Any]:
    from trading_ai.intelligence.resolved_trades import compare_ledger_to_trade_sum, resolve_for_runtime

    gs = store.load_json("goals_state.json")
    active = str(gs.get("active_goal") or "GOAL_A")
    goal = get_goal(active) or get_goal("GOAL_A") or {}
    st_full = dict(state)
    st_full["_raw_trades"] = trades
    rt = resolve_for_runtime(store)
    st_full["goal_truth_discrepancy"] = compare_ledger_to_trade_sum(rt.get("rows_normalized") or [])
    led = load_ledger()
    st_full["ledger_snapshot"] = {
        "realized_pnl_net": float(led.get("realized_pnl_net") or led.get("realized_pnl_usd") or 0.0),
        "rolling_7d_net_profit": float(led.get("rolling_7d_net_profit") or 0.0),
        "rolling_30d_net_profit": float(led.get("rolling_30d_net_profit") or 0.0),
    }
    st_full["capital_total"] = float(net_equity_estimate())
    st_full["current_active_avenues"] = avenue_keys
    ev = evaluate_goal_progress(goal, st_full)
    day_start = datetime.fromtimestamp(now_ts, tz=timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    week_start = day_start - timedelta(days=day_start.weekday())
    t_week = week_start.timestamp()
    by_av = weekly_net_by_avenue(trades, week_start_ts=t_week, now_ts=now_ts)
    plan = gs.get("latest_snapshot") or {}
    dp = plan.get("daily_plan") if isinstance(plan, dict) else {}
    steps_today = []
    steps_tomorrow = []
    if isinstance(dp, dict):
        steps_today = list(dp.get("today_focus") or dp.get("priority_actions") or [])[:10]
        steps_tomorrow = list(dp.get("tomorrow_focus") or [])[:10]

    return {
        "goal_id": active,
        "goal_truth_policy": policy_for_goal(),
        "current_state": ev.get("current_position") or "",
        "target_state": str(goal.get("label") or goal.get("id") or ""),
        "distance_to_goal": None,
        "estimated_progress_rate": ev.get("estimated_days_remaining"),
        "blockers": list(ev.get("blockers") or []),
        "recommended_next_steps_today": steps_today,
        "recommended_next_steps_tomorrow": steps_tomorrow,
        "trajectory_status": ev.get("trajectory_status"),
        "progress_pct": ev.get("progress_pct"),
        "per_avenue_weekly_net": by_av,
        "capital_discrepancy": st_full.get("goal_truth_discrepancy"),
        "honesty": "Goals are evaluated from ledger + trade history; not a promise of future returns.",
    }


def build_global_execution_intelligence_snapshot(
    trades: Optional[List[Dict[str, Any]]] = None,
    *,
    now_ts: Optional[float] = None,
    nte_store: Optional[MemoryStore] = None,
    trade_source: str = "review_resolve",
) -> Dict[str, Any]:
    """
    Full snapshot dict for packets and JSON persistence.

    Default ``trade_source=review_resolve`` uses :func:`resolve_for_review` (federated truth).
    Pass explicit ``trades`` only for tests or when caller pre-resolved rows.
    """
    import time

    ts = now_ts if now_ts is not None else time.time()
    st = nte_store or MemoryStore()
    st.ensure_defaults()

    resolution_note: Dict[str, Any] = {}
    if trades is None or trade_source == "review_resolve":
        rv = resolve_for_review(st)
        trades = rv["rows_for_windows"]
        rt = resolve_for_runtime(st)
        resolution_note = {
            "source_policy_used": policy_for_review(),
            "review_resolution": {k: rv[k] for k in ("truth_version", "data_quality", "federation_meta") if k in rv},
            "discrepancy_runtime_vs_review": build_discrepancy_report(rt, rv),
        }
    else:
        resolution_note = {
            "source_policy_used": {**policy_for_review(), "caller_override": "explicit_trades_list"},
            "honesty": "Caller-supplied trades — verify provenance before governance use.",
        }

    ap = compute_avenue_performance(trades, now_ts=ts)
    ss = derive_system_state_for_ei(trades, now_ts=ts)

    ss_doc = st.load_json("strategy_scores.json")
    gtr = int((ss.get("data_quality") or {}).get("trade_rows") or 0)
    strat = build_strategy_state_summary(ss_doc, global_trade_rows=gtr)

    # Edge stability from strategy score dispersion when present
    sc_vals: List[float] = []
    avb = ss_doc.get("avenues") if isinstance(ss_doc.get("avenues"), dict) else {}
    for _aid, block in avb.items():
        if not isinstance(block, dict):
            continue
        for _sk, row in block.items():
            if not isinstance(row, dict):
                continue
            v = row.get("score")
            if v is not None:
                try:
                    sc_vals.append(float(v))
                except (TypeError, ValueError):
                    pass
    if len(sc_vals) >= 2:
        import statistics

        try:
            sd = statistics.pstdev(sc_vals)
            ss["edge_stability_score"] = max(0.0, min(1.0, 1.0 - min(1.0, sd * 2.0)))
        except statistics.StatisticsError:
            pass

    ca = optimize_capital_allocation(ss, ap)
    perf_bundle = {"avenue_performance": ap, "capital_allocation": ca}
    sc_sig = generate_scaling_signal(ss, perf_bundle)

    av_keys = sorted((ap.get("avenues") or {}).keys())
    try:
        gp = _goal_progress_block(st, ss, trades, now_ts=ts, avenue_keys=av_keys)
    except Exception as exc:
        logger.debug("goal_progress_block: %s", exc)
        gp = {
            "goal_id": "unknown",
            "honesty": "goal_eval_failed",
            "blockers": [str(exc)[:120]],
        }

    daily_plan = {}
    latest = st.load_json("goals_state.json").get("latest_snapshot") or {}
    if isinstance(latest, dict):
        daily_plan = latest.get("daily_plan") or {}

    strongest = ap.get("strongest_avenue") or ""
    weakest = ap.get("weakest_avenue") or ""

    compact = {
        "strongest_avenue": strongest,
        "weakest_avenue": weakest,
        "allocation_top": sorted((ca.get("allocation_map") or {}).items(), key=lambda x: -x[1])[:4],
        "scale": sc_sig.get("scale_action"),
        "scale_factor": sc_sig.get("scale_factor"),
        "goal_id": gp.get("goal_id"),
        "strategy_promoted_n": len(strat.get("promoted_ids") or []),
        "data_label": (ap.get("data_sufficiency") or {}).get("label"),
    }

    return {
        "truth_version": "global_execution_intelligence_v1",
        "generated_at": _iso(),
        "honesty": "Advisory intelligence from closed-trade history and persisted strategy scores — not live orders.",
        "trade_resolution": resolution_note,
        "avenue_performance": ap,
        "capital_allocation": ca,
        "scaling": sc_sig,
        "strategy_state": strat,
        "goals": gp,
        "daily_progression_plan": daily_plan,
        "data_sufficiency": ap.get("data_sufficiency"),
        "system_state_slice": {k: v for k, v in ss.items() if k != "_raw_trades"},
        "execution_intelligence_compact": compact,
    }


def persist_execution_intelligence_artifacts(
    snapshot: Dict[str, Any],
    *,
    review_storage: Any,
    runtime_root: Optional[str] = None,
) -> None:
    """Write canonical JSON files under global memory (ReviewStorage)."""
    st = review_storage
    ap = snapshot.get("avenue_performance") or {}
    st.save_json(
        "avenue_performance.json",
        {
            "truth_version": "avenue_performance_store_v1",
            "generated_at": _iso(),
            "runtime_root": runtime_root,
            "honesty": snapshot.get("honesty"),
            "avenues": ap.get("avenues"),
            "summary": {
                "strongest_avenue": ap.get("strongest_avenue"),
                "weakest_avenue": ap.get("weakest_avenue"),
            },
            "data_sufficiency": ap.get("data_sufficiency"),
        },
    )
    ca = snapshot.get("capital_allocation") or {}
    st.save_json(
        "capital_allocation.json",
        {
            "truth_version": "capital_allocation_store_v1",
            "generated_at": _iso(),
            "runtime_root": runtime_root,
            "allocation_map": ca.get("allocation_map"),
            "reasoning": ca.get("reasoning"),
            "risk_flags": ca.get("risk_flags"),
            "honesty": "Weights are advisory — operator and governance gates still apply.",
        },
    )
    strat = snapshot.get("strategy_state") or {}
    st.save_json(
        "strategy_state.json",
        {
            "truth_version": "strategy_state_store_v1",
            "generated_at": _iso(),
            "runtime_root": runtime_root,
            "strategies": strat.get("strategies"),
            "promoted_ids": strat.get("promoted_ids"),
            "restricted_ids": strat.get("restricted_ids"),
            "source_updated": strat.get("source_updated"),
        },
    )
    st.save_json(
        "global_execution_intelligence_snapshot.json",
        {
            **snapshot,
            "runtime_root": runtime_root,
        },
    )
    gp = snapshot.get("goals") or {}
    st.save_json(
        "goal_progress_snapshot.json",
        {
            "truth_version": "goal_progress_snapshot_v1",
            "generated_at": _iso(),
            "runtime_root": runtime_root,
            "goal_progress": gp,
            "honesty": gp.get("honesty") or snapshot.get("honesty"),
        },
    )

    def _qfile(name: str, items: List[Dict[str, Any]], note: str) -> None:
        raw = st.load_json(name)
        raw["items"] = items[-200:]
        raw["truth_version"] = "ei_queue_v1"
        raw["generated_at"] = _iso()
        raw["honesty"] = note
        st.save_json(name, raw)

    alloc_map = ca.get("allocation_map") or {}
    avenue_focus: List[Dict[str, Any]] = []
    for aid, w in sorted(alloc_map.items(), key=lambda x: -x[1]):
        avenue_focus.append(
            {
                "action_type": "avenue_focus_increase" if w >= 0.35 else "avenue_focus_reduce",
                "avenue": aid,
                "weight": w,
                "advisory": True,
                "enforceable": False,
                "evidence": "capital_allocator",
                "confidence": 0.55 if (snapshot.get("data_sufficiency") or {}).get("label") == "adequate" else 0.35,
            }
        )
    _qfile(
        "avenue_focus_queue.json",
        avenue_focus[:30],
        "Advisory ranking from allocator — not automatic venue routing.",
    )

    cap_items = [
        {
            "action_type": "capital_reallocate",
            "allocation_map": alloc_map,
            "advisory": True,
            "enforceable": False,
            "evidence": "capital_allocator",
            "confidence": 0.5,
        }
    ]
    _qfile("capital_allocation_queue.json", cap_items, "Advisory capital posture snapshot.")

    sc = snapshot.get("scaling") or {}
    scale_action = str(sc.get("scale_action") or "hold")
    st_action = (
        "scale_increase_candidate"
        if scale_action == "increase"
        else ("scale_reduce" if scale_action == "decrease" else "scale_hold")
    )
    _qfile(
        "scaling_queue.json",
        [
            {
                "action_type": st_action,
                "scale_factor": sc.get("scale_factor"),
                "reason": sc.get("reason"),
                "advisory": True,
                "enforceable": False,
                "evidence": "scaling_engine",
                "confidence": sc.get("confidence"),
            }
        ],
        "Advisory scaling posture — does not resize positions.",
    )

    promos: List[Dict[str, Any]] = []
    for sid in strat.get("promoted_ids") or []:
        promos.append(
            {
                "action_type": "promote_strategy_candidate",
                "strategy_id": sid,
                "advisory": True,
                "enforceable": False,
                "evidence": "strategy_manager",
                "confidence": 0.45,
            }
        )
    for sid in strat.get("restricted_ids") or []:
        promos.append(
            {
                "action_type": "restrict_strategy_candidate",
                "strategy_id": sid,
                "advisory": True,
                "enforceable": False,
                "evidence": "strategy_manager",
                "confidence": 0.45,
            }
        )
    _qfile("strategy_promotion_queue.json", promos[:40], "Advisory strategy posture — no automatic promotion.")
