"""CLI: python -m trading_ai shark <subcommand>"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from trading_ai.governance.storage_architecture import shark_audit_log_path
from trading_ai.shark.capital_phase import detect_phase
from trading_ai.shark.gap_hunter import gap_score
from trading_ai.shark.models import GapObservation
from trading_ai.shark.reporting import build_daily_decision_memo, format_daily_summary, format_gap_detection_alert
from trading_ai.shark.state import BAYES, MANDATE
from trading_ai.governance.system_doctrine import is_execution_paused
from trading_ai.shark.state_store import gaps_path, load_capital, load_gaps, load_positions


def _load_json(path: Path) -> Any:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def cmd_status(_: argparse.Namespace) -> int:
    rec = load_capital()
    phase = detect_phase(rec.current_capital)
    g = load_gaps()
    gaps_list = g.get("gaps_under_observation") or []
    print(
        json.dumps(
            {
                "capital": rec.current_capital,
                "phase": phase.value,
                "scan_mode": "24/7",
                "mandate_compounding_paused": MANDATE.compounding_paused,
                "mandate_gaps_paused": MANDATE.gaps_paused,
                "execution_paused": is_execution_paused(),
                "gaps_under_observation": len(gaps_list),
                "trades_today": 0,
                "win_rate_rolling": None,
                "pnl_today": None,
            },
            indent=2,
        )
    )
    return 0


def cmd_gaps(_: argparse.Namespace) -> int:
    print(json.dumps(load_gaps(), indent=2))
    return 0


def cmd_leaderboard(_: argparse.Namespace) -> int:
    print(json.dumps({"strategies": BAYES.strategy_weights, "hunts": BAYES.hunt_weights, "outlets": BAYES.outlet_weights}, indent=2))
    return 0


def cmd_positions(_: argparse.Namespace) -> int:
    print(json.dumps(load_positions(), indent=2))
    return 0


def cmd_audit(_: argparse.Namespace) -> int:
    p = shark_audit_log_path()
    if not p.is_file():
        print("[]")
        return 0
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    out: List[Any] = []
    for line in lines[-500:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    print(json.dumps(out, indent=2))
    return 0


def cmd_pause(_: argparse.Namespace) -> int:
    from trading_ai.shark.state_store import load_execution_control, save_execution_control

    MANDATE.execution_paused = True
    ec = load_execution_control()
    ec["manual_pause"] = True
    save_execution_control(ec)
    print(json.dumps({"ok": True, "execution_paused": True, "scanning_continues": True}))
    return 0


def cmd_pause_compounding(_: argparse.Namespace) -> int:
    MANDATE.compounding_paused = True
    print(json.dumps({"ok": True, "mandate_compounding_paused": True}))
    return 0


def cmd_pause_gaps(_: argparse.Namespace) -> int:
    MANDATE.gaps_paused = True
    print(json.dumps({"ok": True, "mandate_gaps_paused": True}))
    return 0


def cmd_resume(_: argparse.Namespace) -> int:
    from trading_ai.shark.state_store import load_execution_control, save_execution_control

    MANDATE.compounding_paused = False
    MANDATE.gaps_paused = False
    MANDATE.execution_paused = False
    ec = load_execution_control()
    ec["manual_pause"] = False
    save_execution_control(ec)
    print(json.dumps({"ok": True, "resumed": True}))
    return 0


def cmd_health(_: argparse.Namespace) -> int:
    from trading_ai.shark.outlets import default_fetchers
    from trading_ai.shark.scanner import OutletRegistry

    reg = OutletRegistry()
    for f in default_fetchers():
        reg.register(f)
    reg.scan_all()
    print(json.dumps({"outlet_health": reg.last_health, "execution_paused": is_execution_paused()}, indent=2))
    return 0


def cmd_capital(_: argparse.Namespace) -> int:
    rec = load_capital()
    ph = detect_phase(rec.current_capital)
    print(
        json.dumps(
            {
                "current_capital": rec.current_capital,
                "peak_capital": rec.peak_capital,
                "phase": ph.value,
                "monthly_target": rec.monthly_target,
                "monthly_start_capital": rec.monthly_start_capital,
                "progress_to_target": rec.current_capital / max(rec.monthly_target, 1e-6),
            },
            indent=2,
        )
    )
    return 0


def cmd_avenues(_: argparse.Namespace) -> int:
    from trading_ai.shark.avenues import get_avenue_summary

    print(json.dumps(get_avenue_summary(), indent=2))
    return 0


def cmd_dashboard(_: argparse.Namespace) -> int:
    from trading_ai.shark.dashboard import format_dashboard_message, get_master_dashboard

    dash = get_master_dashboard()
    print(format_dashboard_message(dash))
    print()
    print(json.dumps(dash, indent=2))
    return 0


def cmd_sports(args: argparse.Namespace) -> int:
    from trading_ai.shark.avenues import load_avenues
    from trading_ai.shark.sports_tracker import (
        format_sports_picks_message,
        get_daily_picks,
        log_sports_result,
    )

    action = (getattr(args, "action", "") or "").strip()
    if action == "log-result":
        event = getattr(args, "event", None) or "unknown"
        outcome = getattr(args, "outcome", None) or "loss"
        amount = float(getattr(args, "amount", 0) or 0)
        pnl_arg = getattr(args, "pnl", None)
        pnl = float(pnl_arg) if pnl_arg is not None else None
        platform = getattr(args, "platform", None) or "fanduel"
        odds = float(getattr(args, "american_odds", -110.0))
        log_sports_result(
            event_id=event,
            outcome=outcome,
            amount=amount,
            pnl=pnl,
            platform=str(platform),
            american_odds=odds,
        )
        print(json.dumps({"ok": True, "event": event, "outcome": outcome, "amount": amount}))
        return 0
    # default: picks
    avenues = load_avenues()
    bankroll = avenues["sports_manual"].current_capital
    picks = get_daily_picks(bankroll=bankroll)
    print(format_sports_picks_message(picks, bankroll))
    return 0


def cmd_treasury(args: argparse.Namespace) -> int:
    from trading_ai.shark.treasury import get_treasury_summary, log_withdrawal

    action = (getattr(args, "action", "") or "").strip()
    if action == "confirm-withdrawal":
        amount = getattr(args, "amount", None)
        if amount is None:
            print(json.dumps({"error": "--amount required for confirm-withdrawal"}))
            return 1
        log_withdrawal(float(amount))
        print(json.dumps({"ok": True, "withdrawn_usd": round(float(amount), 2)}))
        return 0
    print(json.dumps(get_treasury_summary(), indent=2))
    return 0


def cmd_growth(_: argparse.Namespace) -> int:
    from trading_ai.shark.growth_tracker import format_growth_memo, get_growth_status

    rec = load_capital()
    status = get_growth_status(rec.current_capital, month_start_capital=rec.monthly_start_capital)
    print(format_growth_memo(status))
    print()
    print(json.dumps(status, indent=2))
    return 0


def cmd_networth(_: argparse.Namespace) -> int:
    from trading_ai.shark.treasury import load_treasury

    state = load_treasury()
    print(
        json.dumps(
            {
                "net_worth_usd": state.get("net_worth_usd", 0.0),
                "kalshi_balance_usd": state.get("kalshi_balance_usd", 0.0),
                "manifold_mana_balance": state.get("manifold_mana_balance", 0.0),
                "manifold_usd_balance": state.get("manifold_usd_balance", 0.0),
                "manifold_balance_usd": state.get("manifold_balance_usd", 0.0),
                "last_updated": state.get("last_updated", ""),
            },
            indent=2,
        )
    )
    return 0


def cmd_mana(_: argparse.Namespace) -> int:
    from trading_ai.shark.mana_sandbox import get_mana_summary
    from trading_ai.shark.reporting import format_weekly_mana_section

    s = get_mana_summary()
    print(json.dumps(s, indent=2))
    print()
    print(format_weekly_mana_section())
    perf = s.get("strategy_performance") or {}
    validated = [
        name
        for name, row in perf.items()
        if isinstance(row, dict) and int(row.get("wins", 0) or 0) > 0
    ]
    print()
    print("Strategies with mana wins (signal for Bayesian / real routing):", ", ".join(validated) or "none yet")
    return 0


def main_shark(argv: List[str] | None = None) -> int:
    from trading_ai.shark.dotenv_load import load_shark_dotenv

    load_shark_dotenv()
    argv = argv if argv is not None else sys.argv[1:]
    parser = argparse.ArgumentParser(prog="trading-ai shark")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Capital, phase, scan mode, gaps, P&L")
    sub.add_parser("gaps", help="Gaps under observation + scores")
    sub.add_parser("leaderboard", help="Strategy ranking")
    sub.add_parser("positions", help="Open + pending resolution")
    sub.add_parser("audit", help="Trade audit JSONL")
    sub.add_parser("pause", help="Pause execution; scanning continues 24/7")
    sub.add_parser("pause-compounding", help="Pause compounding mandate only")
    sub.add_parser("pause-gaps", help="Pause gap exploitation only")
    sub.add_parser("resume", help="Resume execution")
    sub.add_parser("health", help="Outlet connectivity + health")
    sub.add_parser("capital", help="Capital, phase, monthly target, progress")

    treasury_p = sub.add_parser("treasury", help="Treasury summary / confirm-withdrawal")
    treasury_p.add_argument("action", nargs="?", default="", help="confirm-withdrawal")
    treasury_p.add_argument("--amount", type=float, help="USD amount for confirm-withdrawal")

    sub.add_parser("growth", help="Growth tracker vs monthly compound targets")
    sub.add_parser("networth", help="Net worth across all platforms")
    sub.add_parser("mana", help="Manifold mana sandbox summary (silent learning)")
    sub.add_parser("avenues", help="All revenue avenues with performance")
    sub.add_parser("dashboard", help="Master dashboard across all avenues + treasury")

    sports_p = sub.add_parser("sports", help="Sports picks (manual) / log result")
    sports_p.add_argument("action", nargs="?", default="picks", help="picks | log-result")
    sports_p.add_argument("--event", help="Event ID for log-result")
    sports_p.add_argument("--outcome", choices=["win", "loss"], help="win or loss")
    sports_p.add_argument("--amount", type=float, help="Amount wagered")
    sports_p.add_argument("--pnl", type=float, help="Actual P&L (optional)")
    sports_p.add_argument("--platform", default="fanduel", help="fanduel | draftkings")
    sports_p.add_argument("--american-odds", type=float, default=-110.0, help="American odds (default -110)")

    args = parser.parse_args(argv)
    return {
        "status": cmd_status,
        "gaps": cmd_gaps,
        "leaderboard": cmd_leaderboard,
        "positions": cmd_positions,
        "audit": cmd_audit,
        "pause": cmd_pause,
        "pause-compounding": cmd_pause_compounding,
        "pause-gaps": cmd_pause_gaps,
        "resume": cmd_resume,
        "health": cmd_health,
        "capital": cmd_capital,
        "treasury": cmd_treasury,
        "growth": cmd_growth,
        "networth": cmd_networth,
        "mana": cmd_mana,
        "avenues": cmd_avenues,
        "dashboard": cmd_dashboard,
        "sports": cmd_sports,
    }[args.cmd](args)


def sample_outputs_for_docs() -> Dict[str, Any]:
    obs = [
        GapObservation("oracle_lag", 45.0, 0.9, 5000.0, 0.12, "none"),
        GapObservation("oracle_lag", 50.0, 0.92, 5200.0, 0.11, "none"),
        GapObservation("oracle_lag", 48.0, 0.91, 5100.0, 0.115, "none"),
        GapObservation("oracle_lag", 52.0, 0.93, 5300.0, 0.118, "none"),
        GapObservation("oracle_lag", 47.0, 0.905, 5150.0, 0.117, "none"),
    ]
    sc = gap_score(obs)
    memo = build_daily_decision_memo(
        hunt_leaderboard={"structural_arbitrage": 0.72},
        outlet_scores={"kalshi": 0.65},
        gaps=["oracle_lag (watch)"],
        phase="phase_1",
    )
    from trading_ai.shark.reporting import startup_banner

    return {
        "gap_score_sample": sc,
        "daily_memo": {
            "hunt_type_notes": memo.hunt_type_notes,
            "outlet_notes": memo.outlet_notes,
            "gaps_observed": memo.gaps_observed,
            "focus_24h": memo.focus_24h,
            "capital_phase": memo.capital_phase,
        },
        "daily_summary": format_daily_summary(
            kalshi_usd=50.0,
            win_rate=0.58,
            best_hunt="structural_arbitrage",
            trades_today=3,
            gaps_monitored=["oracle_lag"],
        ),
        "gap_alert": format_gap_detection_alert(
            gap_type="resolution_data_lag",
            score=0.81,
            edge=0.14,
            volume=10000.0,
            window_duration="minutes to hours",
            recommended_allocation=30.0,
        ),
        "startup": startup_banner(capital=50.0, phase="phase_1", gaps_n=1),
    }
