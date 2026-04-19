"""Capital ledger truth — deposits vs profit, fees, per-avenue buckets, rolling windows."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.nte.paths import nte_capital_ledger_path
from trading_ai.nte.utils.atomic_json import atomic_write_json


def _default_ledger() -> Dict[str, Any]:
    now = time.time()
    return {
        "schema_version": 2,
        "starting_capital": 0.0,
        "capital_added": 0.0,
        "withdrawals": 0.0,
        "realized_pnl_gross": 0.0,
        "realized_fees": 0.0,
        "realized_pnl_net": 0.0,
        "unrealized_pnl": 0.0,
        "current_equity": 0.0,
        "available_cash": 0.0,
        "reserved_cash": 0.0,
        "sandbox_capital": 0.0,
        "per_avenue_allocations": {},
        "per_avenue_realized_net": {},
        "per_avenue_unrealized": {},
        "rolling_7d_net_profit": 0.0,
        "rolling_30d_net_profit": 0.0,
        "entries": [],
        "updated_ts": now,
        # Legacy keys (still written for older readers)
        "starting_capital_usd": 0.0,
        "deposits_usd": 0.0,
        "withdrawals_usd": 0.0,
        "fees_paid_usd": 0.0,
        "realized_pnl_usd": 0.0,
        "unrealized_pnl_usd": 0.0,
        "reserve_usd": 0.0,
        "avenue_allocation": {},
    }


def load_ledger(path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or nte_capital_ledger_path()
    if not p.is_file():
        d = _default_ledger()
        atomic_write_json(p, d)
        return d
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return _migrate_legacy(raw)
        return _default_ledger()
    except Exception:
        return _default_ledger()


def _migrate_legacy(raw: Dict[str, Any]) -> Dict[str, Any]:
    if int(raw.get("schema_version") or 1) >= 2:
        return raw
    out = _default_ledger()
    out.update(raw)
    out["schema_version"] = 2
    out["starting_capital"] = float(raw.get("starting_capital_usd") or raw.get("starting_capital") or 0.0)
    out["capital_added"] = float(raw.get("deposits_usd") or raw.get("capital_added") or 0.0)
    out["withdrawals"] = float(raw.get("withdrawals_usd") or raw.get("withdrawals") or 0.0)
    out["realized_fees"] = float(raw.get("fees_paid_usd") or raw.get("realized_fees") or 0.0)
    out["realized_pnl_net"] = float(raw.get("realized_pnl_usd") or raw.get("realized_pnl_net") or 0.0)
    out["realized_pnl_gross"] = float(raw.get("realized_pnl_gross") or out["realized_pnl_net"]) + float(
        out["realized_fees"]
    )
    out["unrealized_pnl"] = float(raw.get("unrealized_pnl_usd") or raw.get("unrealized_pnl") or 0.0)
    out["reserved_cash"] = float(raw.get("reserve_usd") or raw.get("reserved_cash") or 0.0)
    aa = raw.get("avenue_allocation") or raw.get("per_avenue_allocations") or {}
    if isinstance(aa, dict):
        out["per_avenue_allocations"] = dict(aa)
    return out


def save_ledger(data: Dict[str, Any], path: Optional[Path] = None) -> None:
    data = dict(data)
    data["updated_ts"] = time.time()
    # Sync legacy mirror fields
    data["starting_capital_usd"] = float(data.get("starting_capital") or 0.0)
    data["deposits_usd"] = float(data.get("capital_added") or 0.0)
    data["withdrawals_usd"] = float(data.get("withdrawals") or 0.0)
    data["fees_paid_usd"] = float(data.get("realized_fees") or 0.0)
    data["realized_pnl_usd"] = float(data.get("realized_pnl_net") or 0.0)
    data["unrealized_pnl_usd"] = float(data.get("unrealized_pnl") or 0.0)
    data["reserve_usd"] = float(data.get("reserved_cash") or 0.0)
    data["avenue_allocation"] = dict(data.get("per_avenue_allocations") or {})
    atomic_write_json(path or nte_capital_ledger_path(), data)


def record_deposit(
    amount_usd: float,
    *,
    source: str,
    path: Optional[Path] = None,
) -> None:
    led = load_ledger(path)
    led["capital_added"] = float(led.get("capital_added") or 0.0) + float(amount_usd)
    entries: List[Dict[str, Any]] = list(led.get("entries") or [])
    entries.append(
        {
            "id": str(uuid.uuid4()),
            "ts": time.time(),
            "type": "capital_added",
            "source": source,
            "amount_usd": float(amount_usd),
        }
    )
    led["entries"] = entries[-5000:]
    save_ledger(led, path)


def append_realized(
    amount_usd: float,
    *,
    avenue: str,
    label: str,
    fees_usd: float = 0.0,
    path: Optional[Path] = None,
) -> None:
    """Add realized PnL line; net = gross - fees."""
    led = load_ledger(path)
    gross = float(amount_usd)
    fees = float(fees_usd)
    net = gross - fees
    led["realized_pnl_gross"] = float(led.get("realized_pnl_gross") or 0.0) + gross
    led["realized_fees"] = float(led.get("realized_fees") or 0.0) + fees
    led["realized_pnl_net"] = float(led.get("realized_pnl_net") or 0.0) + net
    per: Dict[str, float] = dict(led.get("per_avenue_realized_net") or {})
    per[avenue] = float(per.get(avenue) or 0.0) + net
    led["per_avenue_realized"] = per
    entries: List[Dict[str, Any]] = list(led.get("entries") or [])
    entries.append(
        {
            "id": str(uuid.uuid4()),
            "ts": time.time(),
            "type": "realized",
            "avenue": avenue,
            "label": label,
            "gross_usd": gross,
            "fees_usd": fees,
            "net_usd": net,
        }
    )
    led["entries"] = entries[-5000:]
    _recompute_rolling(led)
    save_ledger(led, path)


def _recompute_rolling(led: Dict[str, Any]) -> None:
    """Approximate rolling net from recent realized entries (post-fee)."""
    entries: List[Dict[str, Any]] = list(led.get("entries") or [])
    now = time.time()
    net_7 = 0.0
    net_30 = 0.0
    for e in entries:
        if str(e.get("type")) != "realized":
            continue
        try:
            ts = float(e.get("ts") or 0)
            net = float(e.get("net_usd") or 0)
        except (TypeError, ValueError):
            continue
        if now - ts <= 7 * 86400:
            net_7 += net
        if now - ts <= 30 * 86400:
            net_30 += net
    led["rolling_7d_net_profit"] = net_7
    led["rolling_30d_net_profit"] = net_30


def net_equity_estimate(path: Optional[Path] = None) -> float:
    led = load_ledger(path)
    start = float(led.get("starting_capital") or led.get("starting_capital_usd") or 0.0)
    added = float(led.get("capital_added") or led.get("deposits_usd") or 0.0)
    wit = float(led.get("withdrawals") or led.get("withdrawals_usd") or 0.0)
    realized = float(led.get("realized_pnl_net") or led.get("realized_pnl_usd") or 0.0)
    unreal = float(led.get("unrealized_pnl") or led.get("unrealized_pnl_usd") or 0.0)
    return start + added - wit + realized + unreal


def weekly_net_for_goals(path: Optional[Path] = None) -> float:
    """Rolling 7d net profit (post-fee) for Goal B/C."""
    led = load_ledger(path)
    return float(led.get("rolling_7d_net_profit") or 0.0)


def snapshot_for_goals(path: Optional[Path] = None) -> Dict[str, Any]:
    led = load_ledger(path)
    return {
        "equity_estimate": net_equity_estimate(path),
        "weekly_net_profit_usd": weekly_net_for_goals(path),
        "rolling_30d_net_profit": float(led.get("rolling_30d_net_profit") or 0.0),
        "capital_added": float(led.get("capital_added") or 0.0),
        "realized_pnl_net": float(led.get("realized_pnl_net") or 0.0),
    }
