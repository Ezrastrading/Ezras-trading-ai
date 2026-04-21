"""Aggregate performance snapshot for operators."""

import json
from pathlib import Path
from typing import Any, List, Mapping, Union


def update_performance_snapshot(trades: List[Mapping[str, Any]], path: Union[str, Path]) -> None:
    wins = [t for t in trades if float(t.get("net_pnl", 0)) > 0]
    losses = [t for t in trades if float(t.get("net_pnl", 0)) < 0]
    nw = max(len(wins), 1)
    nl = max(len(losses), 1)
    snapshot = {
        "win_rate": len(wins) / max(len(trades), 1),
        "avg_profit": sum(float(t.get("net_pnl", 0)) for t in wins) / nw,
        "avg_loss": sum(float(t.get("net_pnl", 0)) for t in losses) / nl,
        "net_pnl": sum(float(t.get("net_pnl", 0)) for t in trades),
    }
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2)


def refresh_default_performance_snapshot() -> None:
    """Write ``organism/performance_snapshot.json`` from Trade Intelligence events (best-effort)."""
    try:
        from trading_ai.nte.databank.local_trade_store import load_all_trade_events
        from trading_ai.organism.paths import organism_dir

        events = load_all_trade_events()
        trades = [
            {"net_pnl": float(e.get("net_pnl_usd") or e.get("net_pnl") or 0)}
            for e in events
            if isinstance(e, dict)
        ]
        update_performance_snapshot(trades, organism_dir() / "performance_snapshot.json")
    except Exception:
        pass
