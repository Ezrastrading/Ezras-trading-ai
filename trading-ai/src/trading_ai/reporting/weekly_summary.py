"""Rebuild weekly PnL summary from ``trades_clean.csv`` (ISO week, UTC)."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from trading_ai.reporting.paths import weekly_summary_json_path, weekly_summary_txt_path
from trading_ai.reporting.trade_ledger import read_all_clean_rows

logger = logging.getLogger(__name__)


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


def _parse_float(s: str) -> float:
    try:
        return float(s or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _week_id(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _parse_row_date(r: Mapping[str, str]) -> Optional[date]:
    raw = (r.get("date") or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def rebuild_weekly_summary(*, as_of: Optional[date] = None, csv_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Recompute stats for the ISO week containing ``as_of`` (UTC). Writes JSON + TXT.
    """
    anchor = as_of or _today_utc()
    target_week = _week_id(anchor)

    rows = read_all_clean_rows(csv_path)
    week_rows: List[Mapping[str, str]] = []
    for r in rows:
        rd = _parse_row_date(r)
        if rd is None:
            continue
        if _week_id(rd) == target_week:
            week_rows.append(r)

    total_pnl = sum(_parse_float(r.get("net_pnl", "")) for r in week_rows)
    total_trades = len(week_rows)
    wins = sum(1 for r in week_rows if _parse_float(r.get("net_pnl", "")) > 0)
    win_rate = (wins / total_trades * 100.0) if total_trades else 0.0
    avg_pnl = (total_pnl / total_trades) if total_trades else 0.0

    by_day: Dict[date, float] = defaultdict(float)
    for r in week_rows:
        rd = _parse_row_date(r)
        if rd:
            by_day[rd] += _parse_float(r.get("net_pnl", ""))

    best_day = max(by_day.values()) if by_day else 0.0
    worst_day = min(by_day.values()) if by_day else 0.0

    payload: Dict[str, Any] = {
        "week_id": target_week,
        "timezone": "UTC",
        "total_pnl": round(total_pnl, 6),
        "total_trades": total_trades,
        "avg_pnl_per_trade": round(avg_pnl, 6),
        "win_rate_pct": round(win_rate, 2),
        "best_day_pnl": round(best_day, 6),
        "worst_day_pnl": round(worst_day, 6),
        "days_with_trades": sorted(d.isoformat() for d in by_day.keys()),
    }

    jp = weekly_summary_json_path()
    tp = weekly_summary_txt_path()
    try:
        jp.parent.mkdir(parents=True, exist_ok=True)
        jp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tp.write_text(_render_txt(payload), encoding="utf-8")
    except OSError as exc:
        logger.warning("trader_visibility: weekly summary write failed: %s", exc)

    return payload


def _render_txt(p: Mapping[str, Any]) -> str:
    wid = str(p.get("week_id") or "")
    tp = float(p.get("total_pnl") or 0.0)
    sign = "+" if tp >= 0 else ""
    n = int(p.get("total_trades") or 0)
    avg = float(p.get("avg_pnl_per_trade") or 0.0)
    asign = "+" if avg >= 0 else ""
    bd = float(p.get("best_day_pnl") or 0.0)
    wd = float(p.get("worst_day_pnl") or 0.0)
    bds = "+" if bd >= 0 else ""
    wds = "+" if wd >= 0 else ""
    lines = [
        f"WEEK: {wid}",
        "",
        f"TOTAL PNL: {sign}{tp:.2f}",
        f"TRADES: {n}",
        f"AVG PER TRADE: {asign}{avg:.2f}",
        "",
        f"BEST DAY: {bds}{bd:.2f}",
        f"WORST DAY: {wds}{wd:.2f}",
        "",
    ]
    return "\n".join(lines)
