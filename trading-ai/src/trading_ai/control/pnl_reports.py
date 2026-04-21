"""Operator daily CSV + summary under ``data/control``."""

from __future__ import annotations

import csv
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _tail_jsonl(path: Path, n: int = 2000) -> List[Dict[str, Any]]:
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


def _row_day(r: Dict[str, Any]) -> str:
    ts = str(r.get("timestamp") or r.get("closed_at_iso") or r.get("ts") or "")
    if len(ts) >= 10 and ts[4] == "-" and ts[7] == "-":
        return ts[:10]
    ct = r.get("closed_at")
    if isinstance(ct, (int, float)):
        try:
            return datetime.fromtimestamp(float(ct), tz=timezone.utc).strftime("%Y-%m-%d")
        except (TypeError, ValueError, OSError):
            pass
    return ""


def regenerate_daily_pnl_reports() -> None:
    """Rewrite ``daily_trades.csv`` and ``daily_summary.txt`` for UTC today."""
    try:
        from trading_ai.control.paths import daily_summary_operator_path, daily_trades_csv_path
        from trading_ai.reality.trade_logger import trades_raw_path

        day = _utc_day()
        path = trades_raw_path()
        rows = _tail_jsonl(path, 5000)
        today_rows = [r for r in rows if _row_day(r) == day]
        if not today_rows:
            today_rows = rows[-50:] if rows else []

        outp = daily_trades_csv_path()
        outp.parent.mkdir(parents=True, exist_ok=True)
        with outp.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "venue", "product", "entry", "exit", "size", "fees", "net_pnl"])
            for r in today_rows:
                venue = str(r.get("venue") or r.get("avenue") or r.get("outlet") or "")
                prod = str(r.get("product_id") or r.get("asset") or r.get("market_id") or "")
                entry = r.get("entry") or r.get("entry_price") or ""
                exit_ = r.get("exit") or r.get("exit_price") or ""
                size = r.get("size") or r.get("notional_usd") or ""
                fees = r.get("fees") or r.get("fees_usd") or ""
                pnl = r.get("net_pnl_usd", r.get("net_pnl", ""))
                ts = str(r.get("timestamp") or r.get("closed_at") or "")
                w.writerow([ts, venue, prod, entry, exit_, size, fees, pnl])

        by_venue: Dict[str, float] = defaultdict(float)
        pnls: List[float] = []
        for r in today_rows:
            try:
                pv = float(r.get("net_pnl_usd") or r.get("net_pnl") or 0.0)
            except (TypeError, ValueError):
                pv = 0.0
            pnls.append(pv)
            v = str(r.get("venue") or r.get("avenue") or r.get("outlet") or "unknown").lower()
            by_venue[v] += pv
        total = sum(pnls)
        n = len(pnls)
        wins = sum(1 for x in pnls if x > 1e-9)
        wr = (wins / n * 100.0) if n else 0.0
        best = max(pnls) if pnls else 0.0
        worst = min(pnls) if pnls else 0.0

        lines = [
            f"DATE: {day}",
            "",
            f"TOTAL PNL: {total:+.2f}",
            f"TOTAL TRADES: {n}",
            f"WIN RATE: {wr:.0f}%",
            "",
            "BY VENUE:",
        ]
        for k in sorted(by_venue.keys()):
            lines.append(f"- {k}: {by_venue[k]:+.2f}")
        lines.extend(
            [
                "",
                "TOP TRADE:",
                f"  {best:+.2f}",
                "WORST TRADE:",
                f"  {worst:+.2f}",
                "",
            ]
        )
        daily_summary_operator_path().write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception as exc:
        logger.debug("regenerate_daily_pnl_reports: %s", exc)
