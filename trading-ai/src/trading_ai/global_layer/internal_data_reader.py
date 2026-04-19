"""
Capital truth first, then goals, then PnL/trades/rewards.

Normalizes heterogeneous internal sources into one dict for global engines.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from trading_ai.global_layer.global_memory_store import GlobalMemoryStore
from trading_ai.global_layer.trade_truth import avenue_fairness_rollups, load_federated_trades
from trading_ai.nte.capital_ledger import load_ledger, net_equity_estimate
from trading_ai.nte.memory.store import MemoryStore

logger = logging.getLogger(__name__)


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def read_normalized_internal(*, nte_store: Optional[MemoryStore] = None) -> Dict[str, Any]:
    """
    Priority order: capital ledger → global/nte goals → **federated trades** (NTE memory + databank) → rewards → avenues.

    **Trade list:** :func:`trading_ai.global_layer.trade_truth.load_federated_trades` — NTE ``trade_memory.json``
    is primary; databank JSONL enriches matching ``trade_id`` rows and appends databank-only rows (e.g. Kalshi).

    Deposits are reported separately from earned PnL so speed math stays honest.
    """
    ledger = load_ledger()
    deposits = _safe_float(
        ledger.get("capital_added") or ledger.get("deposits_usd")
    )
    withdrawals = _safe_float(ledger.get("withdrawals") or ledger.get("withdrawals_usd"))
    realized = _safe_float(
        ledger.get("realized_pnl_net") or ledger.get("realized_pnl_usd")
    )
    starting = _safe_float(
        ledger.get("starting_capital") or ledger.get("starting_capital_usd")
    )
    equity_from_ledger = net_equity_estimate()

    nte = nte_store or MemoryStore()
    nte.ensure_defaults()
    goals = nte.load_json("goals_state.json")
    reward = nte.load_json("reward_state.json")
    trades, trade_truth_meta = load_federated_trades(nte_store=nte)
    avenue_fairness = avenue_fairness_rollups(trades)

    avenues: Dict[str, Any] = {}
    try:
        from trading_ai.shark.avenues import load_avenues

        avenues = {k: {"status": v.status, "current_capital": v.current_capital} for k, v in load_avenues().items()}
    except Exception as exc:
        logger.debug("avenues load: %s", exc)

    gstore = GlobalMemoryStore()
    gstore.ensure_all()
    speed = gstore.load_json("speed_progression.json")

    return {
        "capital_ledger": {
            "starting_capital_usd": starting,
            "capital_added_usd": deposits,
            "deposits_usd": deposits,
            "withdrawals_usd": withdrawals,
            "realized_pnl_usd": realized,
            "net_equity_estimate_usd": equity_from_ledger,
            "entries_tail": list((ledger.get("entries") or [])[-20:]),
        },
        "goals_state": goals,
        "reward_state": reward,
        "trades": trades,
        "trade_count": len(trades),
        "trade_truth_meta": trade_truth_meta,
        "avenue_fairness": avenue_fairness,
        "avenues": avenues,
        "speed_progression_cache": speed,
        "read_at_ts": time.time(),
    }
