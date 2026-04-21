"""
Human-readable one-screen status: ``data/control/live_status.txt``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _read_json(p: Path) -> Optional[Dict[str, Any]]:
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _tail_jsonl(path: Path, n: int = 1) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()][-n:]
    except OSError:
        return []
    out: List[Dict[str, Any]] = []
    for ln in lines:
        try:
            o = json.loads(ln)
            if isinstance(o, dict):
                out.append(o)
        except json.JSONDecodeError:
            continue
    return out


def _today_utc_prefix() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def write_live_status_snapshot() -> None:
    """Write ``live_status.txt``; never raises."""
    try:
        from trading_ai.control.paths import live_status_path
        from trading_ai.core.system_guard import trading_halt_path
        from trading_ai.global_layer.governance_order_gate import check_new_order_allowed_full
        from trading_ai.organism.deployment_metrics import load_deployment_metrics
        from trading_ai.reality.trade_logger import trades_raw_path
        from trading_ai.shark.state_store import load_positions

        halted = trading_halt_path().is_file()
        status = "HALTED" if halted else "RUNNING"

        try:
            from trading_ai.organism.paths import organism_dir

            _ = _read_json(organism_dir() / "performance_snapshot.json")
        except Exception:
            pass

        day_prefix = _today_utc_prefix()
        raw_p = trades_raw_path()
        today_pnl = 0.0
        win_rate = 0.0
        trades_today = 0
        try:
            from trading_ai.nte.databank.local_trade_store import path_daily_summary

            ds = _read_json(path_daily_summary()) or {}
            by_day = ds.get("by_day") or {}
            if isinstance(by_day, dict) and day_prefix in by_day:
                blob = by_day[day_prefix]
                if isinstance(blob, dict):
                    today_pnl = float(blob.get("net_pnl") or blob.get("net_pnl_usd") or 0.0)
                    trades_today = int(blob.get("trade_count") or blob.get("n") or 0)
                    wr = blob.get("win_rate")
                    if wr is not None:
                        win_rate = float(wr) * 100.0 if float(wr) <= 1.0 else float(wr)
        except Exception:
            pass
        if trades_today <= 0:
            rows = _tail_jsonl(raw_p, 500)
            today_pnls: List[float] = []
            wins = 0
            for r in rows:
                ts = str(r.get("timestamp") or r.get("closed_at") or r.get("ts") or "")
                if len(ts) >= 10:
                    if ts[:10] != day_prefix:
                        continue
                elif day_prefix not in ts:
                    continue
                pnl = r.get("net_pnl_usd")
                if pnl is None:
                    pnl = r.get("net_pnl")
                try:
                    pv = float(pnl or 0.0)
                except (TypeError, ValueError):
                    pv = 0.0
                today_pnls.append(pv)
                trades_today += 1
                if pv > 1e-9:
                    wins += 1
            today_pnl = sum(today_pnls)
            win_rate = (wins / trades_today * 100.0) if trades_today > 0 else 0.0

        pos = load_positions()
        open_n = len(pos.get("open_positions") or [])

        last_trade = "n/a"
        last_rows = _tail_jsonl(raw_p, 1)
        if last_rows:
            lr = last_rows[-1]
            lp = lr.get("net_pnl_usd", lr.get("net_pnl", 0))
            try:
                lpv = float(lp or 0.0)
            except (TypeError, ValueError):
                lpv = 0.0
            prod = str(lr.get("product_id") or lr.get("asset") or lr.get("market_id") or "unknown")
            last_trade = f"{lpv:+.2f} ({prod})"

        dm = load_deployment_metrics()
        supa_ok = int(dm.get("supabase_failures") or 0) == 0
        exec_ok = int(dm.get("execution_errors") or 0) == 0
        supa_l = "OK" if supa_ok else "FAIL"
        exec_l = "OK" if exec_ok else "FAIL"

        gov_l = "BLOCKED"
        gov_mode = "?"
        gov_integrity = "?"
        gov_allowed = "?"
        gov_reason = "?"
        try:
            ok, reason, gov_audit = check_new_order_allowed_full(venue="coinbase", log_decision=False)
            gov_l = "OK" if ok else "BLOCKED"
            gov_mode = str(gov_audit.get("live_mode") or "?")
            gov_integrity = str(gov_audit.get("review_integrity_state") or "?")
            gov_allowed = str(bool(gov_audit.get("allowed")))
            gov_reason = str(gov_audit.get("reason_code") or "?")
        except Exception:
            gov_l = "BLOCKED"

        risk = "NORMAL"
        if halted:
            risk = "HALTED"
        else:
            try:
                from trading_ai.global_layer.governance_order_gate import load_joint_review_snapshot

                jr = load_joint_review_snapshot()
                mode = str(jr.get("live_mode") or "").lower()
                if mode == "paused":
                    risk = "HALTED"
                elif mode == "caution":
                    risk = "REDUCED"
            except Exception:
                pass

        lines = [
            f"STATUS: {status}",
            f"TODAY PNL: {today_pnl:+.2f}",
            f"OPEN POSITIONS: {open_n}",
            f"LAST TRADE: {last_trade}",
            "",
            f"WIN RATE TODAY: {win_rate:.0f}%",
            "",
            "SYSTEM HEALTH:",
            f"- Supabase: {supa_l}",
            f"- Execution: {exec_l}",
            f"- Governance: {gov_l}",
            "",
            "GOVERNANCE:",
            f"- mode: {gov_mode}",
            f"- integrity: {gov_integrity}",
            f"- allowed: {gov_allowed}",
            f"- reason: {gov_reason}",
            "",
            f"RISK MODE: {risk}",
            "",
        ]

        live_status_path().write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception as exc:
        logger.debug("write_live_status_snapshot: %s", exc)
