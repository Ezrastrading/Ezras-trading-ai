"""
Append-only human-readable clean trade ledger (CSV).

Read-only with respect to trading decisions; local files only.
"""

from __future__ import annotations

import csv
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from trading_ai.reporting.paths import trades_clean_csv_path

logger = logging.getLogger(__name__)

CSV_HEADERS: List[str] = [
    "timestamp",
    "date",
    "venue",
    "market",
    "instrument_kind",
    "side",
    "notional_usd",
    "base_qty",
    "entry_price",
    "exit_price",
    "fees_paid",
    "net_pnl",
    "gross_pnl",
    "expected_edge_bps",
    "execution_quality_score",
    "latency_ms",
    "hold_seconds",
    "regime",
    "edge_id",
    "edge_status",
    "result",
]


def _parse_ts(ts: Any) -> Optional[datetime]:
    if ts is None or (isinstance(ts, str) and not str(ts).strip()):
        return None
    s = str(ts).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _f(x: Any) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _round_num(x: Optional[float], nd: int) -> str:
    if x is None:
        return ""
    try:
        return f"{float(x):.{nd}f}"
    except (TypeError, ValueError):
        return ""


def _result_from_net(net: float) -> str:
    if net > 1e-12:
        return "WIN"
    if net < -1e-12:
        return "LOSS"
    return "FLAT"


def _notional_usd(trade: Mapping[str, Any]) -> Optional[float]:
    n = _f(trade.get("notional_usd"))
    if n is not None and n > 0:
        return n
    bq = _f(trade.get("base_qty"))
    ep = _f(trade.get("actual_entry_price")) or _f(trade.get("avg_entry_price")) or _f(trade.get("entry_price"))
    if bq is not None and ep is not None:
        return abs(bq * ep)
    c = _f(trade.get("contracts"))
    ppc = _f(trade.get("entry_price_per_contract"))
    if c is not None and ppc is not None:
        return abs(c * ppc)
    return None


def build_clean_row(trade_dict: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Map a closed-trade dict to CSV columns. Returns None if required fields are missing.

    Required: parsable ``net_pnl``, and ``timestamp_close`` (or ``timestamp`` for legacy paths).
    """
    net = _f(trade_dict.get("net_pnl_usd"))
    if net is None:
        net = _f(trade_dict.get("net_pnl"))
    if net is None:
        logger.warning("trader_visibility: skip row — missing net_pnl")
        return None

    ts_close_raw = trade_dict.get("timestamp_close") or trade_dict.get("timestamp")
    dt_close = _parse_ts(ts_close_raw)
    if dt_close is None:
        logger.warning("trader_visibility: skip row — missing/invalid timestamp_close")
        return None

    ts_open_raw = trade_dict.get("timestamp_open")
    dt_open = _parse_ts(ts_open_raw) if ts_open_raw else None

    hold_s: Optional[float] = _f(trade_dict.get("hold_seconds"))
    if hold_s is None and dt_open and dt_close:
        hold_s = max(0.0, (dt_close - dt_open).total_seconds())

    venue = str(trade_dict.get("avenue_name") or trade_dict.get("venue") or "").strip()
    market = str(trade_dict.get("asset") or trade_dict.get("market") or trade_dict.get("product") or "").strip()
    ik = str(trade_dict.get("instrument_kind") or "").strip()
    side = str(trade_dict.get("side") or trade_dict.get("direction") or "").strip()

    fees = _f(trade_dict.get("fees_paid"))
    if fees is None:
        fees = _f(trade_dict.get("fees_usd")) or _f(trade_dict.get("fees"))
    gross = _f(trade_dict.get("gross_pnl"))
    if gross is None:
        gross = net + (fees or 0.0)

    entry_p = _f(trade_dict.get("actual_entry_price")) or _f(trade_dict.get("avg_entry_price")) or _f(
        trade_dict.get("entry_price")
    )
    exit_p = _f(trade_dict.get("actual_exit_price")) or _f(trade_dict.get("avg_exit_price")) or _f(
        trade_dict.get("exit_price")
    )

    bq = _f(trade_dict.get("base_qty")) or _f(trade_dict.get("base_size"))

    lat = _f(trade_dict.get("execution_latency_ms")) or _f(trade_dict.get("latency_ms"))

    eqs = _f(trade_dict.get("execution_quality_score"))

    exp_edge = _f(trade_dict.get("expected_edge_bps"))

    regime = str(trade_dict.get("regime") or "").strip()

    eid = str(trade_dict.get("edge_id") or "").strip()
    est = str(trade_dict.get("edge_status_at_trade") or trade_dict.get("edge_status") or "").strip()

    nominal = _notional_usd(trade_dict)

    d_iso = dt_close.date().isoformat()

    return {
        "timestamp": dt_close.isoformat(),
        "date": d_iso,
        "venue": venue,
        "market": market,
        "instrument_kind": ik,
        "side": side,
        "notional_usd": nominal,
        "base_qty": bq,
        "entry_price": entry_p,
        "exit_price": exit_p,
        "fees_paid": fees,
        "net_pnl": net,
        "gross_pnl": gross,
        "expected_edge_bps": exp_edge,
        "execution_quality_score": eqs,
        "latency_ms": lat,
        "hold_seconds": hold_s,
        "regime": regime,
        "edge_id": eid,
        "edge_status": est,
        "result": _result_from_net(net),
    }


def _format_csv_row(row: Dict[str, Any]) -> Dict[str, str]:
    net = float(row["net_pnl"])
    out: Dict[str, str] = {}
    out["timestamp"] = str(row.get("timestamp") or "")
    out["date"] = str(row.get("date") or "")
    out["venue"] = str(row.get("venue") or "")
    out["market"] = str(row.get("market") or "")
    out["instrument_kind"] = str(row.get("instrument_kind") or "")
    out["side"] = str(row.get("side") or "")
    n = row.get("notional_usd")
    out["notional_usd"] = _round_num(_f(n), 2) if n is not None else ""
    out["base_qty"] = _round_num(_f(row.get("base_qty")), 6)
    out["entry_price"] = _round_num(_f(row.get("entry_price")), 6)
    out["exit_price"] = _round_num(_f(row.get("exit_price")), 6)
    out["fees_paid"] = _round_num(_f(row.get("fees_paid")), 4)
    out["net_pnl"] = _round_num(net, 4)
    out["gross_pnl"] = _round_num(_f(row.get("gross_pnl")), 4)
    out["expected_edge_bps"] = _round_num(_f(row.get("expected_edge_bps")), 4)
    out["execution_quality_score"] = _round_num(_f(row.get("execution_quality_score")), 4)
    out["latency_ms"] = _round_num(_f(row.get("latency_ms")), 2)
    out["hold_seconds"] = _round_num(_f(row.get("hold_seconds")), 2)
    out["regime"] = str(row.get("regime") or "")
    out["edge_id"] = str(row.get("edge_id") or "")
    out["edge_status"] = str(row.get("edge_status") or "")
    out["result"] = _result_from_net(net)
    return out


def append_clean_trade_row(trade_dict: Mapping[str, Any]) -> bool:
    """
    Append one row to ``trades_clean.csv``. Returns False if row skipped or I/O error (logged).
    """
    row = build_clean_row(trade_dict)
    if row is None:
        return False
    formatted = _format_csv_row(row)
    path = trades_clean_csv_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.is_file()
    try:
        with path.open("a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=CSV_HEADERS, extrasaction="ignore")
            if new_file:
                w.writeheader()
            w.writerow({k: formatted.get(k, "") for k in CSV_HEADERS})
        return True
    except OSError as exc:
        logger.warning("trader_visibility: trades_clean.csv append failed: %s", exc)
        return False


def read_all_clean_rows(path: Optional[Path] = None) -> List[Dict[str, str]]:
    """Load all rows from trades_clean.csv (for summaries)."""
    p = path or trades_clean_csv_path()
    if not p.is_file():
        return []
    rows: List[Dict[str, str]] = []
    try:
        with p.open("r", encoding="utf-8", newline="") as f:
            r = csv.DictReader(f)
            for line in r:
                if line:
                    rows.append(dict(line))
    except OSError as exc:
        logger.warning("trader_visibility: read trades_clean.csv failed: %s", exc)
    return rows
