"""Master wallet ledger — Coinbase treasury + per-avenue profit attribution (JSON under runtime)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from trading_ai.governance.storage_architecture import shark_state_path

logger = logging.getLogger(__name__)


def master_wallet_path() -> Path:
    return shark_state_path("master_wallet.json")


def default_master_wallet() -> Dict[str, Any]:
    return {
        "total_deposited": 0.0,
        "total_profit": 0.0,
        "by_avenue": {
            "kalshi": {"deposited": 0.0, "profit": 0.0},
            "robinhood": {"deposited": 0.0, "profit": 0.0},
            "tastytrade": {"deposited": 0.0, "profit": 0.0},
            "coinbase": {"deposited": 0.0, "profit": 0.0},
            "manifold": {"deposited": 0.0, "profit": 0.0},
            "metaculus": {"deposited": 0.0, "profit": 0.0},
            "polymarket": {"deposited": 0.0, "profit": 0.0},
        },
        "current_total": 0.0,
        "month_target": 1750.0,
        "on_track": False,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


def load_master_wallet() -> Dict[str, Any]:
    p = master_wallet_path()
    if not p.exists():
        d = default_master_wallet()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d, indent=2), encoding="utf-8")
        return dict(d)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default_master_wallet()


def save_master_wallet(state: Dict[str, Any]) -> None:
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    p = master_wallet_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")


def sync_master_wallet_from_runtime(balance_sync_result: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """
    Merge treasury + balance_sync snapshot into ``by_avenue`` / ``current_total``.
    Does not withdraw — attribution only.
    """
    state = load_master_wallet()
    try:
        from trading_ai.shark.treasury import get_treasury_summary

        t = get_treasury_summary()
    except Exception:
        t = {}
    kalshi = float(t.get("kalshi_balance_usd", 0.0) or 0.0)
    poly = float(t.get("polymarket_balance_usd", 0.0) or 0.0)
    mana = float(t.get("manifold_mana_balance", 0.0) or 0.0)
    musd = float(t.get("manifold_usd_balance", 0.0) or 0.0)
    net = float(t.get("net_worth_usd", 0.0) or 0.0)
    by = dict(state.get("by_avenue") or {})
    if "kalshi" not in by:
        by["kalshi"] = {"deposited": 0.0, "profit": 0.0}
    by["kalshi"]["deposited"] = round(max(by["kalshi"].get("deposited", 0.0), kalshi), 2)
    if "polymarket" not in by:
        by["polymarket"] = {"deposited": 0.0, "profit": 0.0}
    by["polymarket"]["deposited"] = round(poly, 2)
    if "manifold" not in by:
        by["manifold"] = {"deposited": 0.0, "profit": 0.0}
    by["manifold"]["deposited"] = round(mana + musd, 2)
    try:
        from trading_ai.shark.coinbase_tracker import get_coinbase_balance

        cb = get_coinbase_balance()
        cu = float(cb.get("usdc", 0) or 0) + float(cb.get("eth_usd_value", 0) or 0)
        if "coinbase" not in by:
            by["coinbase"] = {"deposited": 0.0, "profit": 0.0}
        by["coinbase"]["deposited"] = round(cu, 2)
    except Exception:
        pass
    state["by_avenue"] = by
    cb_dep = float((by.get("coinbase") or {}).get("deposited", 0.0) or 0.0)
    state["current_total"] = round(net + cb_dep, 2)
    deposited = float(state.get("total_deposited", 0.0) or 0.0)
    if deposited <= 0.0:
        deposited = float(t.get("total_deposited_usd", 10.0) or 10.0)
        state["total_deposited"] = round(deposited, 2)
    state["total_profit"] = round(float(t.get("total_profit_usd", 0.0) or 0.0), 2)
    mt = float(state.get("month_target", 1750.0) or 1750.0)
    state["month_target"] = mt
    state["on_track"] = bool(state["current_total"] >= mt * 0.5)
    _ = balance_sync_result
    save_master_wallet(state)
    return state
