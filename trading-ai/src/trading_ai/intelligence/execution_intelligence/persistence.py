"""Persist EIE snapshots to ``goals_state.json`` (NTE memory)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.intelligence.eie_nte_artifacts import write_execution_intelligence_nte_bundle
from trading_ai.intelligence.execution_intelligence.ceo_session_store import append_structured_ceo_session, build_session_record_from_eie
from trading_ai.intelligence.execution_intelligence.daily_plan import generate_daily_plan
from trading_ai.intelligence.execution_intelligence.evaluation import (
    attach_raw_trades,
    evaluate_goal_progress,
)
from trading_ai.intelligence.execution_intelligence.goals import GOAL_A, GOAL_B, GOAL_C, GOAL_D, get_goal
from trading_ai.intelligence.execution_intelligence.system_state import (
    get_system_state,
    global_weekly_totals_by_iso_week,
)
from trading_ai.intelligence.ts_parse import last_n_iso_week_ids
from trading_ai.intelligence.resolved_trades import build_discrepancy_report, resolve_for_review, resolve_for_runtime
from trading_ai.intelligence.truth_contract import policy_for_goal, summarize_policies
from trading_ai.nte.memory.store import MemoryStore
from trading_ai.nte.paths import nte_memory_dir

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _net_ledger(state: Dict[str, Any]) -> float:
    led = state.get("ledger_snapshot") or {}
    try:
        return float(led.get("realized_pnl_net") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def goal_a_met(state: Dict[str, Any]) -> bool:
    return _net_ledger(state) >= 1000.0 - 1e-6


def goal_b_met(trades: List[Dict[str, Any]], now_ts: float) -> bool:
    buckets = global_weekly_totals_by_iso_week(trades, now_ts=now_ts)
    last_two = last_n_iso_week_ids(now_ts, 2)
    if len(last_two) < 2:
        return False
    return all(buckets.get(wid, 0.0) >= 1000.0 - 1e-6 for wid in last_two)


def goal_cd_met(
    goal_id: str,
    state: Dict[str, Any],
    trades: List[Dict[str, Any]],
    now_ts: float,
) -> bool:
    from trading_ai.intelligence.execution_intelligence.system_state import weekly_net_by_avenue

    target = 2000.0 if goal_id == GOAL_C else 3000.0
    day_start = datetime.fromtimestamp(now_ts, tz=timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    week_start = day_start - timedelta(days=day_start.weekday())
    t_week = week_start.timestamp()
    by_av = weekly_net_by_avenue(trades, week_start_ts=t_week, now_ts=now_ts)
    avenues = list(state.get("current_active_avenues") or [])
    if not avenues:
        return False
    for av in avenues:
        if by_av.get(av, 0.0) < target - 1e-6:
            return False
    return len(avenues) > 0


def select_active_goal(
    state: Dict[str, Any],
    trades: List[Dict[str, Any]],
    *,
    now_ts: Optional[float] = None,
) -> str:
    """Pick the first goal in A→D order that is not yet met (honest gates)."""
    import time

    ts = now_ts if now_ts is not None else time.time()

    if not goal_a_met(state):
        return GOAL_A
    if not goal_b_met(trades, ts):
        return GOAL_B
    if not goal_cd_met(GOAL_C, state, trades, ts):
        return GOAL_C
    if not goal_cd_met(GOAL_D, state, trades, ts):
        return GOAL_D
    return GOAL_D


def load_goals_state(store: MemoryStore) -> Dict[str, Any]:
    store.ensure_defaults()
    return store.load_json("goals_state.json")


def _migrate_gs(raw: Dict[str, Any]) -> Dict[str, Any]:
    if int(raw.get("schema_version") or 0) >= 2:
        return raw
    out = {
        "schema_version": 2,
        "active_goal": raw.get("active_goal") or GOAL_A,
        "progress_history": list(raw.get("progress_history") or []),
        "last_update": raw.get("last_update") or raw.get("updated"),
        "daily_plan_history": list(raw.get("daily_plan_history") or []),
        "milestones": {
            "GOAL_A": bool(raw.get("goal_1k_60d", {}).get("met")) if isinstance(raw.get("goal_1k_60d"), dict) else False,
        },
    }
    return out


def refresh_execution_intelligence(
    store: Optional[MemoryStore] = None,
    *,
    now_ts: Optional[float] = None,
    persist: bool = True,
    closed_trade_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Full EIE snapshot: system state, goal progress, daily plan, persisted history.

    Canonical refresh owner: **post-trade closed** (pass ``closed_trade_id`` for idempotency).
    Safe to call on a schedule; does not trade.
    """
    st = store or MemoryStore()
    st.ensure_defaults()
    gs = _migrate_gs(load_goals_state(st))

    if (
        persist
        and closed_trade_id
        and str(gs.get("last_eie_refresh_trade_id") or "") == str(closed_trade_id)
        and gs.get("last_eie_bundle")
    ):
        return dict(gs["last_eie_bundle"])

    rv = resolve_for_runtime(st)
    trades_for_goals = rv["rows_for_windows"]

    state = get_system_state(store=st, now_ts=now_ts)
    state = attach_raw_trades(state, trades_for_goals)

    import time

    from trading_ai.runtime_paths import ezras_runtime_root

    ts = now_ts if now_ts is not None else time.time()
    active = select_active_goal(state, trades_for_goals, now_ts=ts)
    goal = get_goal(active) or get_goal(GOAL_A) or {}
    progress = evaluate_goal_progress(goal, state)
    progress["goal_truth_policy"] = policy_for_goal()
    progress["capital_ledger_vs_trade_sum"] = state.get("goal_truth_discrepancy")
    plan = generate_daily_plan(goal, state)

    rr = resolve_for_review(st)
    disc = build_discrepancy_report(rv, rr)
    truth_summary = {
        "truth_version": "truth_source_summary_v1",
        "runtime_nte_rows_usable": len(rv.get("rows_for_windows") or []),
        "review_federated_rows_usable": len(rr.get("rows_for_windows") or []),
        "policies": summarize_policies(),
        "honesty": "Runtime uses NTE memory; review intelligence may federate databank — compare discrepancy report.",
    }

    last_update = _now_iso()
    rr_path = None
    try:
        rr_path = str(ezras_runtime_root())
    except Exception:
        pass

    out: Dict[str, Any] = {
        "active_goal": active,
        "goal": goal,
        "progress": progress,
        "daily_plan": plan,
        "system_state": {k: v for k, v in state.items() if k != "_raw_trades"},
        "goals_state_path": str(st.path("goals_state.json")),
        "truth_contracts": summarize_policies(),
        "trade_discrepancy_report": disc,
        "truth_source_summary": truth_summary,
    }

    if persist:
        gs["active_goal"] = active
        gs["last_update"] = last_update
        if closed_trade_id:
            gs["last_eie_refresh_trade_id"] = str(closed_trade_id)

        ph = list(gs.get("progress_history") or [])
        ph.append(
            {
                "ts": last_update,
                "goal_id": active,
                "progress_pct": progress.get("progress_pct"),
                "trajectory": progress.get("trajectory_status"),
                "blockers": progress.get("blockers"),
            }
        )
        gs["progress_history"] = ph[-200:]

        dph = list(gs.get("daily_plan_history") or [])
        dph.append({"ts": last_update, "goal_id": active, "plan": plan})
        gs["daily_plan_history"] = dph[-90:]

        gs["latest_snapshot"] = {
            "system_state": {k: v for k, v in state.items() if k != "_raw_trades"},
            "goal_progress": progress,
            "daily_plan": plan,
        }
        gs["schema_version"] = 2
        gs["last_eie_bundle"] = out

        try:
            ceo_rec = build_session_record_from_eie(
                active_goal=active,
                progress=progress,
                daily_plan=plan,
                avenue_focus=list((state.get("current_active_avenues") or [])[:8]),
            )
            append_structured_ceo_session(ceo_rec)
        except Exception as exc:
            logger.debug("structured ceo session append: %s", exc)

        try:
            write_execution_intelligence_nte_bundle(
                out,
                discrepancy_report=disc,
                truth_source_summary=truth_summary,
                runtime_root=rr_path,
            )
        except Exception as exc:
            logger.warning("eie nte artifacts: %s", exc)
        try:
            write_goal_progress_snapshot_nte(progress, runtime_root=rr_path)
        except Exception as exc:
            logger.debug("goal_progress_snapshot_nte: %s", exc)

        try:
            st.save_json("goals_state.json", gs)
        except OSError as exc:
            logger.warning("goals_state save failed: %s", exc)

    return out


def write_goal_progress_snapshot_nte(progress: Dict[str, Any], *, runtime_root: Optional[str] = None) -> Path:
    """Standalone goal progress JSON for operators."""
    p = nte_memory_dir() / "goal_progress_snapshot.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "truth_version": "goal_progress_snapshot_v2",
        "generated_at": _now_iso(),
        "runtime_root": runtime_root,
        "goal_progress": progress,
        "source_policy": policy_for_goal(),
    }
    p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return p
