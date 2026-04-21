"""Per-strategy and aggregate PnL from simulated / federated-style trade dicts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _net(t: Dict[str, Any]) -> float:
    for k in ("net_pnl_usd", "net_pnl"):
        v = t.get(k)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return 0.0


def strategy_key(t: Dict[str, Any]) -> str:
    return str(t.get("strategy_id") or t.get("setup_type") or "unknown")


def build_pnl_rollup(
    trades: List[Dict[str, Any]],
    *,
    append_point: Optional[float] = None,
    history_tail: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Roll up net PnL by strategy plus total. Optionally append a scalar net point for drift windows.
    """
    by_strat: Dict[str, Dict[str, Any]] = {}
    total = 0.0
    wins = 0
    n = 0
    for t in trades:
        if not isinstance(t, dict):
            continue
        st = str(t.get("status") or "").lower()
        if st and st not in ("closed", "settled", "filled"):
            continue
        sk = strategy_key(t)
        net = _net(t)
        total += net
        n += 1
        if net > 0:
            wins += 1
        row = by_strat.setdefault(sk, {"net_usd": 0.0, "trades": 0, "wins": 0})
        row["net_usd"] = float(row["net_usd"]) + net
        row["trades"] = int(row["trades"]) + 1
        if net > 0:
            row["wins"] = int(row["wins"]) + 1

    hist = list(history_tail or [])
    if append_point is not None:
        hist.append({"t": _iso(), "net_session_usd": float(append_point)})
    hist = hist[-240:]

    win_rate = (wins / n) if n else None
    return {
        "truth_version": "sim_pnl_rollup_v1",
        "generated_at": _iso(),
        "trade_rows_used": n,
        "net_total_usd": round(total, 6),
        "win_rate": None if win_rate is None else round(win_rate, 4),
        "by_strategy": {k: {**v, "net_usd": round(float(v["net_usd"]), 6)} for k, v in sorted(by_strat.items())},
        "rolling_points": hist,
        "honesty": "PnL from in-memory trade rows only; simulation does not fetch live markets.",
    }
