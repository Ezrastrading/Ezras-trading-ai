"""CLI for institutional hardening: execution, lockouts, truth-sync, memo, exceptions, governance."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, List, Optional


def _print_json(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str))


def main_execution(argv: Optional[List[str]] = None) -> int:
    from trading_ai.execution.execution_reconciliation import (
        get_execution_reconciliation_status,
        get_last_reconciliation,
        reconcile_execution_intent_vs_result,
        record_execution_close,
    )

    p = argparse.ArgumentParser(prog="trading-ai execution")
    sub = p.add_subparsers(dest="act", required=True)
    sub.add_parser("reconcile-status", help="Summary of persisted reconciliation state")
    p_last = sub.add_parser("show-last", help="Last reconciliation record")
    p_sim = sub.add_parser("simulate-reconcile", help="Dry-run reconcile (stdout only)")
    p_sim.add_argument("--trade-id", default="sim")
    p_sim.add_argument("--requested", type=float, default=100.0)
    p_sim.add_argument("--approved", type=float, default=50.0)
    p_sim.add_argument("--submitted", type=float, default=50.0)
    p_sim.add_argument("--filled", type=float, default=50.0)
    p_sim.add_argument("--avg-fill", type=float, default=0.42)
    p_sim.add_argument("--expected", type=float, default=0.41)
    p_sim.add_argument("--fees", type=float, default=None)
    p_close = sub.add_parser("record-close", help="Attach close PnL to reconciliation record")
    p_close.add_argument("--trade-id", required=True)
    p_close.add_argument("--pnl", type=float, default=None)
    args, _ = p.parse_known_args(argv)
    if args.act == "reconcile-status":
        _print_json(get_execution_reconciliation_status())
        return 0
    if args.act == "show-last":
        _print_json(get_last_reconciliation())
        return 0
    if args.act == "simulate-reconcile":
        r = reconcile_execution_intent_vs_result(
            trade_id=args.trade_id,
            requested_size=args.requested,
            approved_size=args.approved,
            submitted_size=args.submitted,
            filled_size=args.filled,
            avg_fill_price=args.avg_fill,
            expected_entry_price=args.expected,
            fees=args.fees,
        )
        _print_json(r)
        return 0
    if args.act == "record-close":
        _print_json(record_execution_close(trade_id=args.trade_id, realized_pnl=args.pnl))
        return 0
    return 1


def main_lockouts(argv: Optional[List[str]] = None) -> int:
    from trading_ai.risk import hard_lockouts as hl

    p = argparse.ArgumentParser(prog="trading-ai lockouts")
    sub = p.add_subparsers(dest="act", required=True)
    sub.add_parser("status", help="Effective lockout snapshot")
    p_sd = sub.add_parser("simulate-daily-loss", help="Set daily loss %% drill")
    p_sd.add_argument("--pct", type=float, required=True)
    p_sw = sub.add_parser("simulate-weekly-drawdown", help="Set weekly DD %% drill")
    p_sw.add_argument("--pct", type=float, required=True)
    sub.add_parser("clear-daily", help="Legacy clear daily (records governance event)")
    sub.add_parser("clear-weekly", help="Legacy clear weekly (records governance event)")
    p_cwm = sub.add_parser(
        "clear-weekly-manual",
        help="Explicit weekly lockout clear (actor + reason required)",
    )
    p_cwm.add_argument("--actor", required=True)
    p_cwm.add_argument("--reason", required=True)
    args, _ = p.parse_known_args(argv)
    if args.act == "status":
        _print_json(hl.get_effective_lockout())
        return 0
    if args.act == "simulate-daily-loss":
        _print_json(hl.simulate_daily_loss(args.pct))
        return 0
    if args.act == "simulate-weekly-drawdown":
        _print_json(hl.simulate_weekly_drawdown(args.pct))
        return 0
    if args.act == "clear-daily":
        _print_json(hl.clear_daily_override())
        return 0
    if args.act == "clear-weekly":
        _print_json(hl.clear_weekly_override())
        return 0
    if args.act == "clear-weekly-manual":
        _print_json(hl.clear_weekly_lockout_manual(actor=args.actor, reason=args.reason))
        return 0
    return 1


def main_truth_sync(argv: Optional[List[str]] = None) -> int:
    from trading_ai.execution.venue_truth_sync import run_truth_sync, simulate_drift, truth_sync_status

    p = argparse.ArgumentParser(prog="trading-ai truth-sync")
    sub = p.add_subparsers(dest="act", required=True)
    sub.add_parser("status", help="Last venue truth sync row")
    p_run = sub.add_parser("run", help="Run sync (mock adapter, or --kalshi when configured)")
    p_run.add_argument("--kalshi", action="store_true", help="Use Kalshi adapter (UNSUPPORTED if not configured)")
    sub.add_parser("simulate-drift", help="Force material drift scenario")
    args, rest = p.parse_known_args(argv)
    if args.act == "status":
        _print_json(truth_sync_status())
        return 0
    if args.act == "run":
        fac = "kalshi" if getattr(args, "kalshi", False) else "mock"
        _print_json(
            run_truth_sync(
                internal_open_ids=[],
                internal_cash=None,
                adapter_factory=fac,
            )
        )
        try:
            from trading_ai.ops.automation_heartbeat import record_heartbeat

            record_heartbeat("truth_sync", ok=True, note="truth-sync run")
        except Exception:
            pass
        return 0
    if args.act == "simulate-drift":
        _print_json(simulate_drift())
        return 0
    _ = rest
    return 1


def main_operational(argv: Optional[List[str]] = None) -> int:
    """Operational readiness: final-gap-check (distinct from ``phase_institutional`` CLI)."""
    if not argv:
        return 1
    if argv[0] == "final-gap-check":
        from trading_ai.ops.final_gap_check import run_final_gap_check

        _print_json(run_final_gap_check())
        return 0
    return 1


def main_memo(argv: Optional[List[str]] = None) -> int:
    from trading_ai.reporting.daily_decision_memo import generate_daily_memo, show_last_memo

    p = argparse.ArgumentParser(prog="trading-ai memo")
    sub = p.add_subparsers(dest="act", required=True)
    sub.add_parser("generate-daily", help="Write daily_decision_memo.md")
    sub.add_parser("show-last", help="Print last memo body")
    args, _ = p.parse_known_args(argv)
    if args.act == "generate-daily":
        _print_json(generate_daily_memo())
        try:
            from trading_ai.ops.automation_heartbeat import record_heartbeat

            record_heartbeat("memo_generation", ok=True, note="generate-daily")
        except Exception:
            pass
        return 0
    if args.act == "show-last":
        _print_json(show_last_memo())
        return 0
    return 1


def main_exceptions(argv: Optional[List[str]] = None) -> int:
    from trading_ai.ops.exception_dashboard import dashboard_status, list_open_exceptions, mark_resolved

    p = argparse.ArgumentParser(prog="trading-ai exceptions")
    sub = p.add_subparsers(dest="act", required=True)
    sub.add_parser("status", help="Open exception count")
    sub.add_parser("show-open", help="List unresolved entries")
    p_mr = sub.add_parser("mark-resolved", help="Mark entry resolved by id")
    p_mr.add_argument("--id", dest="entry_id", required=True)
    args, _ = p.parse_known_args(argv)
    if args.act == "status":
        _print_json(dashboard_status())
        return 0
    if args.act == "show-open":
        _print_json({"entries": list_open_exceptions()})
        return 0
    if args.act == "mark-resolved":
        _print_json(mark_resolved(args.entry_id))
        return 0
    return 1


def main_governance(argv: Optional[List[str]] = None) -> int:
    from trading_ai.governance import parameter_governance as pg

    p = argparse.ArgumentParser(prog="trading-ai governance")
    sub = p.add_subparsers(dest="act", required=True)
    sub.add_parser("show-params", help="Snapshot tracked parameters")
    sub.add_parser("show-recent", help="Recent governance records")
    p_rc = sub.add_parser("record-change", help="Record a parameter change")
    p_rc.add_argument("--name", required=True)
    p_rc.add_argument("--old", dest="old_value", required=True)
    p_rc.add_argument("--new", dest="new_value", required=True)
    p_rc.add_argument("--reason", required=True)
    p_rc.add_argument("--by", default="operator")
    p_rc.add_argument("--impact", default="governance")
    args, _ = p.parse_known_args(argv)
    if args.act == "show-params":
        _print_json(pg.snapshot_tracked_parameters())
        return 0
    if args.act == "show-recent":
        _print_json({"changes": pg.get_recent_parameter_changes()})
        return 0
    if args.act == "record-change":
        _print_json(
            pg.record_parameter_change(
                parameter_name=args.name,
                old_value=args.old_value,
                new_value=args.new_value,
                reason=args.reason,
                changed_by=args.by,
                impact_area=args.impact,
            )
        )
        return 0
    return 1


def main_sizing_explain(argv: Optional[List[str]] = None) -> int:
    from trading_ai.automation.adaptive_sizing import explain_multiplier_decision, get_ladder_state

    p = argparse.ArgumentParser(prog="trading-ai sizing-explain")
    p.add_argument("--bucket", default="NORMAL")
    args = p.parse_args(argv)
    _print_json(
        {
            "ladder": get_ladder_state(),
            "explain": explain_multiplier_decision(args.bucket),
        }
    )
    return 0
