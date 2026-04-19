"""
Avenue-local microstructure hints from internal trades (Coinbase first).

Promotes only durable counts to ``market_knowledge``; noise stays local.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, List


def learn_from_trades(trades: List[Dict[str, Any]], *, avenue: str = "coinbase") -> Dict[str, Any]:
    by_asset = defaultdict(lambda: {"wins": 0, "losses": 0, "net": 0.0})
    regimes = Counter()
    for t in trades:
        av = str(t.get("avenue") or t.get("avenue_id") or "coinbase").strip()
        if avenue != "all" and av != avenue:
            continue
        aid = str(t.get("setup_type") or t.get("asset") or "unknown")
        net = float(t.get("net_pnl_usd") or 0.0)
        by_asset[aid]["net"] += net
        if net >= 0:
            by_asset[aid]["wins"] += 1
        else:
            by_asset[aid]["losses"] += 1
        rg = str(t.get("regime") or "unknown")
        regimes[rg] += 1
    return {
        "avenue": avenue,
        "assets": {k: dict(v) for k, v in by_asset.items()},
        "regime_histogram": dict(regimes),
    }


def merge_into_market_knowledge(base: Dict[str, Any], learned: Dict[str, Any]) -> Dict[str, Any]:
    avenues = base.setdefault("avenues", {})
    cb = avenues.setdefault("coinbase", {})
    cb.setdefault("assets", {}).update(learned.get("assets") or {})
    cb.setdefault("regime_patterns", []).append(
        {"histogram": learned.get("regime_histogram"), "source": "internal_trades"}
    )
    cb["regime_patterns"] = cb["regime_patterns"][-50:]
    return base
