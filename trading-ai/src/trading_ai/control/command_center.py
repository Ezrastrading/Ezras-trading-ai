"""
Unified control room snapshot — aggregates read-only organism state.

Never places trades or mutates execution.

Tracked path: ``src/trading_ai/control/command_center.py`` (operator brief via :func:`run_command_center_snapshot`).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from trading_ai.control.command_center_models import empty_snapshot
from trading_ai.control.paths import command_center_report_path, command_center_snapshot_path

logger = logging.getLogger(__name__)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.debug("command_center read %s: %s", path, exc)
        return None


def _tail_jsonl(path: Path, n: int = 50) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    lines: List[str] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            lines = f.readlines()[-n:]
    except OSError:
        return []
    out: List[Dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            if isinstance(rec, dict):
                out.append(rec)
        except json.JSONDecodeError:
            continue
    return out


def _edge_status_counts(edges: List[Mapping[str, Any]]) -> Dict[str, int]:
    c: Dict[str, int] = {}
    for e in edges:
        st = str(e.get("status") or "unknown").lower()
        c[st] = c.get(st, 0) + 1
    return c


def _confidence_label(n: int, expectancy: float) -> str:
    if n < 5:
        return "low"
    if n < 20:
        return "medium"
    if n < 50:
        return "high"
    if expectancy > 0 and n >= 50:
        return "strong"
    return "high"


def _equity_trend(curve: List[float]) -> str:
    if len(curve) < 3:
        return "flat"
    a = sum(curve[-3:]) / 3
    b = sum(curve[-6:-3]) / 3 if len(curve) >= 6 else a
    if a > b * 1.01:
        return "up"
    if a < b * 0.99:
        return "down"
    return "flat"


def gather_command_center_inputs(
    *,
    runtime_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Collect paths and raw dicts; safe when files missing."""
    from trading_ai.core.system_guard import trading_halt_path
    from trading_ai.edge.paths import edge_registry_path
    from trading_ai.global_layer.governance_order_gate import (
        check_new_order_allowed_full,
        load_joint_review_snapshot,
    )
    from trading_ai.governance.storage_architecture import global_memory_dir
    from trading_ai.intelligence.deployment_decision import deployment_status_path
    from trading_ai.monitoring.execution_monitor import execution_metrics_path
    from trading_ai.nte.databank.local_trade_store import path_daily_summary, path_weekly_summary
    from trading_ai.nte.paths import nte_system_health_path
    from trading_ai.organism.deployment_metrics import deployment_metrics_path, load_deployment_metrics
    from trading_ai.organism.paths import failsafe_state_path, organism_dir
    from trading_ai.reality.edge_truth import edge_truth_summary_path
    from trading_ai.reality.paths import trade_logs_dir
    from trading_ai.review.paths import ceo_daily_review_json_path, daily_diagnosis_path
    from trading_ai.learning.paths import improvement_history_path, trading_memory_path
    from trading_ai.capital.router import venue_scores_path
    from trading_ai.core.portfolio_engine import portfolio_state_path
    from trading_ai.edge.registry import EdgeRegistry

    _ = runtime_root  # reserved for future path overrides
    halt = trading_halt_path()
    halt_present = halt.is_file()
    halt_payload = _read_json(halt) or {}
    deployment_status = _read_json(deployment_status_path())
    deploy_metrics = load_deployment_metrics()
    portfolio = _read_json(portfolio_state_path())
    joint = load_joint_review_snapshot()
    _ok, _reason, gate_audit = check_new_order_allowed_full(venue="coinbase", log_decision=False)
    gate_audit = dict(gate_audit)
    gate_audit["allowed"] = _ok
    gate_audit["reason"] = _reason

    reg = EdgeRegistry(path=edge_registry_path())
    raw_edges = reg.load_raw().get("edges") or []
    status_counts = _edge_status_counts(raw_edges)

    by_eid: Dict[str, float] = {}
    for e in raw_edges:
        if not isinstance(e, dict):
            continue
        exp = e.get("post_fee_expectancy")
        if exp is None:
            continue
        try:
            by_eid[str(e.get("edge_id"))] = float(exp)
        except (TypeError, ValueError):
            pass
    et = _read_json(edge_truth_summary_path()) or {}
    for eid, blob in (et.get("edges") or {}).items():
        if str(eid) in by_eid:
            continue
        if not isinstance(blob, dict):
            continue
        wins = blob.get("windows") or {}
        ne = None
        if isinstance(wins, dict):
            for pref in ("100", "50", "20"):
                w = wins.get(pref)
                if isinstance(w, dict) and w.get("net_expectancy") is not None:
                    try:
                        ne = float(w["net_expectancy"])
                        break
                    except (TypeError, ValueError):
                        ne = None
        if ne is not None:
            by_eid[str(eid)] = ne
    post_fee_expectancies = list(by_eid.items())
    post_fee_expectancies.sort(key=lambda x: x[1], reverse=True)
    top5 = post_fee_expectancies[:5]
    worst5 = list(reversed(post_fee_expectancies[-5:])) if post_fee_expectancies else []

    exec_path = execution_metrics_path()
    exec_lines = _tail_jsonl(exec_path, 200)
    slips = [float(x["slippage_bps"]) for x in exec_lines if x.get("slippage_bps") is not None]
    lats = [float(x["latency_ms"]) for x in exec_lines if x.get("latency_ms") is not None]
    avg_slip = sum(slips) / len(slips) if slips else None
    avg_lat = sum(lats) / len(lats) if lats else None
    last_slip = slips[-1] if slips else None
    anomalies_exec = sum(1 for x in exec_lines if x.get("degraded") or x.get("anomaly"))

    daily = _read_json(path_daily_summary()) or {}
    weekly = _read_json(path_weekly_summary()) or {}
    perf_snap = _read_json(organism_dir() / "performance_snapshot.json") or {}

    diagnosis = _read_json(daily_diagnosis_path())
    ceo = _read_json(ceo_daily_review_json_path())
    memory = _read_json(trading_memory_path())
    improvement_tail = _tail_jsonl(improvement_history_path(), 20)

    venue_scores = _read_json(venue_scores_path())
    failsafe = _read_json(failsafe_state_path())
    nte_health = _read_json(nte_system_health_path())

    gdir = global_memory_dir()
    execution_intelligence_snapshot = _read_json(gdir / "global_execution_intelligence_snapshot.json")
    goal_progress_snapshot = _read_json(gdir / "goal_progress_snapshot.json")
    avenue_focus_queue = _read_json(gdir / "avenue_focus_queue.json")

    operator_unified = {}
    try:
        from trading_ai.control.operator_unified_status import build_operator_unified_status

        operator_unified = build_operator_unified_status()
    except Exception as exc:
        logger.debug("operator_unified_status: %s", exc)
        operator_unified = {"error": str(exc)}

    trade_logs_dir_p = trade_logs_dir()

    supa_ok = int(deploy_metrics.get("supabase_failures") or 0) == 0

    return {
        "timestamp": _iso_now(),
        "halt_present": halt_present,
        "halt_reason": str(halt_payload.get("reason") or ""),
        "deployment_status": deployment_status,
        "deploy_metrics": deploy_metrics,
        "portfolio": portfolio,
        "joint_review": joint,
        "gate_audit": gate_audit,
        "edge_status_counts": status_counts,
        "edges_sample": raw_edges[:200],
        "top_edges_post_fee": top5,
        "worst_edges_post_fee": worst5,
        "execution_metrics_path": str(exec_path),
        "avg_slippage_bps": avg_slip,
        "latest_slippage_bps": last_slip,
        "avg_latency_ms": avg_lat,
        "execution_anomalies_recent": anomalies_exec,
        "daily_summary": daily,
        "weekly_summary": weekly,
        "performance_snapshot": perf_snap,
        "diagnosis": diagnosis,
        "ceo_review": ceo,
        "memory": memory or {},
        "improvement_tail": improvement_tail,
        "venue_scores": venue_scores,
        "failsafe": failsafe,
        "nte_health": nte_health,
        "execution_intelligence_snapshot": execution_intelligence_snapshot or {},
        "goal_progress_snapshot": goal_progress_snapshot or {},
        "avenue_focus_queue": avenue_focus_queue or {},
        "operator_unified_status": operator_unified,
        "trade_logs_dir": str(trade_logs_dir_p),
        "supabase_ok_heuristic": supa_ok,
    }


def build_alerts(payload: Mapping[str, Any]) -> List[Dict[str, Any]]:
    alerts: List[Dict[str, Any]] = []
    if payload.get("halt_present"):
        alerts.append({"level": "CRITICAL", "message": f"Trading halt: {payload.get('halt_reason')}", "source": "halt"})
    dm = payload.get("deploy_metrics") or {}
    if int(dm.get("supabase_failures") or 0) > 0:
        alerts.append({"level": "CRITICAL", "message": "Supabase failures recorded in deployment metrics", "source": "supabase"})
    jr = payload.get("joint_review") or {}
    if jr.get("live_mode") == "paused":
        alerts.append({"level": "CRITICAL", "message": "Joint review live_mode paused", "source": "governance"})
    ga = payload.get("gate_audit") or {}
    if not ga.get("allowed", True):
        alerts.append(
            {
                "level": "CRITICAL",
                "message": f"Governance blocking: {ga.get('reason')}",
                "source": "governance_gate",
            }
        )
    avg_slip = payload.get("avg_slippage_bps")
    if avg_slip is not None and float(avg_slip) > 40:
        alerts.append({"level": "WARNING", "message": "Slippage elevated vs baseline", "source": "execution"})
    diag = payload.get("diagnosis") or {}
    if diag.get("health") == "bad":
        alerts.append({"level": "WARNING", "message": "Daily diagnosis health bad", "source": "diagnosis"})
    if diag.get("metrics", {}).get("anomaly_count", 0) >= 5:
        alerts.append({"level": "WARNING", "message": "Execution anomaly count elevated", "source": "diagnosis"})
    alerts.append({"level": "INFO", "message": "Command center snapshot refreshed", "source": "control_room"})
    return alerts


def compose_snapshot(inputs: Mapping[str, Any]) -> Dict[str, Any]:
    ts = str(inputs.get("timestamp") or _iso_now())
    snap = empty_snapshot(ts)

    dm = inputs.get("deploy_metrics") or {}
    ds = inputs.get("deployment_status") or {}
    halt = bool(inputs.get("halt_present"))
    jr = inputs.get("joint_review") or {}
    ga = inputs.get("gate_audit") or {}
    portfolio = inputs.get("portfolio") or {}
    diag = inputs.get("diagnosis") or {}
    rr = diag.get("risk_recommendation") or {}
    metrics = diag.get("metrics") or {}
    ceo = inputs.get("ceo_review") or {}
    mem = inputs.get("memory") or {}

    snap["system_health"] = {
        "halted": halt,
        "halt_reason": inputs.get("halt_reason") or None,
        "supabase_sync_ok": bool(inputs.get("supabase_ok_heuristic")),
        "data_pipeline_ok": True,
        "execution_ok": float(inputs.get("avg_slippage_bps") or 0) < 80,
        "scheduler_ok": None,
        "governance_ok": jr.get("live_mode") in ("normal", "caution", None) or jr.get("live_mode") is None,
        "deployment_ready": bool(dm.get("DEPLOYMENT_READY")),
        "ready_for_first_20": bool(dm.get("READY_FOR_FIRST_20")),
        "all_blockers_green": not halt and jr.get("live_mode") != "paused" and ga.get("allowed", True),
    }

    snap["deployment_status"] = {
        "execution_success": dm.get("execution_success"),
        "coinbase_order_verified": dm.get("coinbase_order_verified"),
        "databank_written": dm.get("databank_written"),
        "supabase_synced": dm.get("supabase_synced"),
        "governance_logged": dm.get("governance_logged"),
        "packet_updated": dm.get("packet_updated"),
        "scheduler_stable": dm.get("scheduler_stable"),
        "FINAL_EXECUTION_PROVEN": dm.get("FINAL_EXECUTION_PROVEN"),
        "READY_FOR_FIRST_20": dm.get("READY_FOR_FIRST_20"),
        "profit_reality": {
            "ready": ds.get("ready"),
            "reason": ds.get("reason"),
            "expectancy": ds.get("expectancy"),
            "drawdown": ds.get("drawdown"),
        },
    }

    mets = metrics
    snap["risk_state"] = {
        "current_risk_mode": None,
        "recommended_risk_mode": rr.get("risk_mode"),
        "current_size_multiplier": None,
        "drawdown": ds.get("drawdown"),
        "consecutive_losses": None,
        "system_guard_triggered_or_near": halt or int(mets.get("anomaly_count") or 0) >= 3,
        "max_exposure": None,
        "avenue_capital_fractions": (portfolio.get("capital_fraction_by_avenue") if portfolio else {}) or {},
        "size_multiplier_recommendation": rr.get("size_multiplier_recommendation"),
    }

    snap["governance_state"] = {
        "live_mode_recommendation": jr.get("live_mode"),
        "review_integrity_state": jr.get("integrity"),
        "confidence_score": None,
        "caution_block_entries_env": (os.environ.get("GOVERNANCE_CAUTION_BLOCK_ENTRIES") or "").strip() or None,
        "trade_entry_blocked": not ga.get("allowed", True),
        "block_reason": ga.get("reason"),
        "joint_review_id": jr.get("joint_review_id"),
        "stale_joint_review": jr.get("stale"),
    }

    vs = inputs.get("venue_scores") or {}
    venue_state: Dict[str, Any] = {}
    scores = vs.get("venues") if isinstance(vs, dict) else None
    if isinstance(scores, dict):
        venue_state = scores
    else:
        for a, pnl in (portfolio.get("cumulative_pnl_by_avenue") or {}).items():
            venue_state[str(a)] = {
                "capital_allocated_fraction": (portfolio.get("capital_fraction_by_avenue") or {}).get(a),
                "pnl_cumulative": pnl,
                "shutdown_flag": False,
                "execution_quality": None,
                "venue_score": None,
                "active": True,
            }
    snap["venue_state"] = venue_state

    ec = inputs.get("edge_status_counts") or {}
    snap["edge_state"] = {
        "counts_by_status": ec,
        "total_edges": sum(ec.values()) if ec else 0,
        "top_5_post_fee_expectancy": inputs.get("top_edges_post_fee") or [],
        "worst_5_post_fee_expectancy": inputs.get("worst_edges_post_fee") or [],
        "edge_promotion_events_today": None,
        "failsafe": inputs.get("failsafe"),
    }

    snap["execution_state"] = {
        "average_latency_ms": inputs.get("avg_latency_ms"),
        "latest_slippage_bps": inputs.get("latest_slippage_bps"),
        "average_slippage_bps": inputs.get("avg_slippage_bps"),
        "execution_anomalies_count": inputs.get("execution_anomalies_recent"),
        "fee_drag_estimate": None,
        "execution_killing_edge": float(inputs.get("avg_slippage_bps") or 0) > 50,
    }

    ps = inputs.get("performance_snapshot") or {}
    daily = inputs.get("daily_summary") or {}
    trades_today = int(daily.get("trade_count") or daily.get("trades") or 0)
    win_rate = float(ps.get("win_rate") or daily.get("win_rate") or 0.0)
    net_day = float(daily.get("net_pnl") or metrics.get("net_pnl") or 0.0)
    net_week = float((inputs.get("weekly_summary") or {}).get("net_pnl") or 0.0)
    expectancy = float(daily.get("expectancy") or ps.get("expectancy") or metrics.get("rolling_expectancy") or 0.0)
    profits = [float(x) for x in (ps.get("trade_pnls") or [])] if ps.get("trade_pnls") else []
    profit_factor = None
    if profits:
        pos = sum(x for x in profits if x > 0)
        neg = abs(sum(x for x in profits if x < 0))
        profit_factor = (pos / neg) if neg > 1e-9 else None
    snap["performance_state"] = {
        "total_trades_today": trades_today,
        "total_trades_week": int((inputs.get("weekly_summary") or {}).get("trade_count") or 0),
        "win_rate": win_rate,
        "avg_win": ps.get("avg_profit"),
        "avg_loss": ps.get("avg_loss"),
        "net_pnl_today": net_day,
        "net_pnl_week": net_week,
        "expectancy": expectancy,
        "profit_factor": profit_factor,
        "equity_curve_trend": _equity_trend([float(x) for x in (ps.get("equity_curve") or [])])
        if ps.get("equity_curve")
        else "flat",
        "confidence_level": _confidence_label(int(daily.get("trade_count") or trades_today or 0), expectancy),
    }

    snap["learning_state"] = {
        "repeating_mistakes": mem.get("repeated_mistakes") or [],
        "repeating_strengths": mem.get("repeated_strengths") or [],
        "latest_lesson": (inputs.get("improvement_tail") or [{}])[-1] if inputs.get("improvement_tail") else None,
        "latest_avenue_lesson": mem.get("avenue_summaries"),
        "recommendation_that_worked": (mem.get("recommendations_that_worked") or [])[:1],
        "recommendation_that_failed": (mem.get("recommendations_that_failed") or [])[:1],
    }

    snap["ceo_state"] = {
        "key_problems": diag.get("key_problems") or ceo.get("what_to_improve"),
        "key_strengths": diag.get("key_strengths") or ceo.get("where_edge_is_strengthening"),
        "biggest_risk": diag.get("biggest_risk") or (ceo.get("executive_summary") or {}).get("biggest_risk"),
        "biggest_opportunity": diag.get("best_opportunity"),
        "recommended_actions": diag.get("recommended_actions") or ceo.get("recommended_actions"),
        "what_to_improve": ceo.get("what_to_improve"),
        "what_to_avoid": ceo.get("what_to_avoid"),
        "what_to_implement_next": ceo.get("what_to_implement_next"),
        "what_to_scale": ceo.get("what_to_scale"),
        "what_to_pause": ceo.get("what_to_pause"),
    }

    snap["portfolio_state"] = portfolio or {"note": "portfolio_state.json missing or empty"}

    snap["alerts"] = build_alerts(
        {
            "halt_present": halt,
            "halt_reason": inputs.get("halt_reason"),
            "deploy_metrics": dm,
            "joint_review": jr,
            "gate_audit": ga,
            "avg_slippage_bps": inputs.get("avg_slippage_bps"),
            "diagnosis": diag,
        }
    )

    snap["supplemental"] = {
        "nte_system_health": inputs.get("nte_health"),
        "trade_logs_dir": inputs.get("trade_logs_dir"),
    }
    snap["operator_runtime_summary"] = inputs.get("operator_unified_status") or {}
    return snap


def render_human_report(snap: Mapping[str, Any]) -> str:
    sh = snap.get("system_health") or {}
    perf = snap.get("performance_state") or {}
    es = snap.get("edge_state") or {}
    vs = snap.get("venue_state") or {}
    lines = [
        "COMMAND CENTER",
        "==============",
        f"Timestamp: {snap.get('timestamp')}",
        "",
        "SYSTEM",
        f"  Health blockers clear: {sh.get('all_blockers_green')}",
        f"  Halted: {sh.get('halted')}",
        f"  Deployment ready: {sh.get('deployment_ready')}",
        f"  Risk recommendation: {(snap.get('risk_state') or {}).get('recommended_risk_mode')}",
        "",
        "PERFORMANCE",
        f"  Trades today: {perf.get('total_trades_today')}",
        f"  Net PnL (today): {perf.get('net_pnl_today')}",
        f"  Win rate: {perf.get('win_rate')}",
        f"  Expectancy: {perf.get('expectancy')}",
        "",
        "TOP EDGES (post-fee expectancy field when present)",
    ]
    for row in es.get("top_5_post_fee_expectancy") or []:
        lines.append(f"  - {row}")
    lines.append("")
    lines.append("BOTTOM EDGES")
    for row in es.get("worst_5_post_fee_expectancy") or []:
        lines.append(f"  - {row}")
    lines.append("")
    lines.append("VENUES")
    if isinstance(vs, dict):
        for k, v in list(vs.items())[:12]:
            lines.append(f"  - {k}: {v}")
    lines.append("")
    lines.append("ALERTS")
    for a in snap.get("alerts") or []:
        lines.append(f"  [{a.get('level')}] {a.get('message')}")
    lines.append("")
    lines.append("CEO NOTE")
    ceo = snap.get("ceo_state") or {}
    for key in ("biggest_risk", "biggest_opportunity", "what_to_implement_next"):
        if ceo.get(key):
            lines.append(f"  {key}: {ceo.get(key)}")
    lines.append("")
    return "\n".join(lines)


def run_command_center_snapshot(
    *,
    write_files: bool = True,
    runtime_root: Optional[Path] = None,
) -> Dict[str, Any]:
    inputs = gather_command_center_inputs(runtime_root=runtime_root)
    snap = compose_snapshot(inputs)
    if write_files:
        outp = command_center_snapshot_path()
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(snap, indent=2, default=str), encoding="utf-8")
        command_center_report_path().write_text(render_human_report(snap), encoding="utf-8")
    try:
        from trading_ai.control.live_status import write_live_status_snapshot

        write_live_status_snapshot()
    except Exception:
        logger.debug("live_status snapshot skipped", exc_info=True)
    try:
        from trading_ai.control.heartbeat import run_heartbeat_check

        run_heartbeat_check()
    except Exception:
        logger.debug("heartbeat skipped", exc_info=True)
    return snap
