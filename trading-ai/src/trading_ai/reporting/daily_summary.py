"""Rebuild daily PnL summary from ``trades_clean.csv`` (UTC day)."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from trading_ai.reporting.paths import daily_summary_json_path, daily_summary_txt_path
from trading_ai.reporting.trade_ledger import read_all_clean_rows

logger = logging.getLogger(__name__)


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


def _parse_float(s: str) -> float:
    try:
        return float(s or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _rows_for_day(rows: List[Mapping[str, str]], day: date) -> List[Mapping[str, str]]:
    d = day.isoformat()
    out: List[Mapping[str, str]] = []
    for r in rows:
        if (r.get("date") or "").strip() == d:
            out.append(r)
    return out


def rebuild_daily_summary(*, as_of: Optional[date] = None, csv_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Recompute stats for ``as_of`` (UTC date). Writes JSON + TXT under ``data/trade_logs/``.
    """
    day = as_of or _today_utc()
    rows = read_all_clean_rows(csv_path)
    day_rows = _rows_for_day(rows, day)

    total_pnl = sum(_parse_float(r.get("net_pnl", "")) for r in day_rows)
    total_trades = len(day_rows)
    wins = sum(1 for r in day_rows if _parse_float(r.get("net_pnl", "")) > 0)
    win_rate = (wins / total_trades * 100.0) if total_trades else 0.0
    avg_pnl = (total_pnl / total_trades) if total_trades else 0.0

    by_venue: Dict[str, Dict[str, float]] = {}
    by_market: Dict[str, Dict[str, float]] = {}
    for r in day_rows:
        v = (r.get("venue") or "unknown").strip() or "unknown"
        m = (r.get("market") or "unknown").strip() or "unknown"
        p = _parse_float(r.get("net_pnl", ""))
        if v not in by_venue:
            by_venue[v] = {"pnl": 0.0, "trades": 0.0}
        by_venue[v]["pnl"] += p
        by_venue[v]["trades"] += 1.0
        if m not in by_market:
            by_market[m] = {"pnl": 0.0, "trades": 0.0}
        by_market[m]["pnl"] += p
        by_market[m]["trades"] += 1.0

    best_trade: Dict[str, Any] = {"net_pnl": None, "market": ""}
    worst_trade: Dict[str, Any] = {"net_pnl": None, "market": ""}
    for r in day_rows:
        p = _parse_float(r.get("net_pnl", ""))
        mk = (r.get("market") or "").strip()
        if best_trade["net_pnl"] is None or p > float(best_trade["net_pnl"]):
            best_trade = {"net_pnl": p, "market": mk}
        if worst_trade["net_pnl"] is None or p < float(worst_trade["net_pnl"]):
            worst_trade = {"net_pnl": p, "market": mk}

    payload: Dict[str, Any] = {
        "date": day.isoformat(),
        "timezone": "UTC",
        "total_pnl": round(total_pnl, 6),
        "total_trades": total_trades,
        "win_rate_pct": round(win_rate, 2),
        "avg_pnl_per_trade": round(avg_pnl, 6),
        "by_venue": {k: {"pnl": round(v["pnl"], 6), "trades": int(v["trades"])} for k, v in by_venue.items()},
        "by_market": {k: {"pnl": round(v["pnl"], 6), "trades": int(v["trades"])} for k, v in by_market.items()},
        "best_trade": best_trade,
        "worst_trade": worst_trade,
    }

    jp = daily_summary_json_path()
    tp = daily_summary_txt_path()
    try:
        jp.parent.mkdir(parents=True, exist_ok=True)
        jp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tp.write_text(_render_txt(payload), encoding="utf-8")
    except OSError as exc:
        logger.warning("trader_visibility: daily summary write failed: %s", exc)

    return payload


def _venue_label(name: str) -> str:
    n = (name or "").strip().lower()
    if n == "coinbase":
        return "Coinbase"
    if n == "kalshi":
        return "Kalshi"
    if not n:
        return "unknown"
    return n[:1].upper() + n[1:]


def _render_txt(p: Mapping[str, Any]) -> str:
    d = str(p.get("date") or "")
    tp = float(p.get("total_pnl") or 0.0)
    sign = "+" if tp >= 0 else ""
    n = int(p.get("total_trades") or 0)
    wr = float(p.get("win_rate_pct") or 0.0)

    lines = [
        f"DATE: {d}",
        "",
        f"TOTAL PNL: {sign}{tp:.2f}",
        f"TRADES: {n}",
        f"WIN RATE: {wr:.0f}%",
        "",
        "BY VENUE:",
    ]
    bv = p.get("by_venue") or {}
    if not bv:
        lines.append("- (none)")
    else:
        for venue, agg in sorted(bv.items()):
            pn = float(agg.get("pnl") or 0.0)
            tr = int(agg.get("trades") or 0)
            ps = "+" if pn >= 0 else ""
            lines.append(f"- {_venue_label(venue)}: {ps}{pn:.2f} ({tr} trades)")

    bt = p.get("best_trade") or {}
    wt = p.get("worst_trade") or {}
    bpn = bt.get("net_pnl")
    wpn = wt.get("net_pnl")
    best_line = "n/a"
    worst_line = "n/a"
    if n > 0 and bpn is not None:
        best_line = f"+{float(bpn):.2f} on {bt.get('market') or 'n/a'}"
    if n > 0 and wpn is not None:
        worst_line = f"{float(wpn):.2f} on {wt.get('market') or 'n/a'}"
    lines.extend(
        [
            "",
            "BEST TRADE:",
            best_line,
            "",
            "WORST TRADE:",
            worst_line,
            "",
        ]
    )
    return "\n".join(lines)
