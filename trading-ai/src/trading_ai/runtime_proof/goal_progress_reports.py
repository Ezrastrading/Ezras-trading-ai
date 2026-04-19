"""
Per-avenue and global goal snapshots — JSON artifacts for operator review (no trading).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.global_layer.trade_truth import avenue_fairness_rollups, load_federated_trades
from trading_ai.nte.memory.store import MemoryStore

GOAL_A_USD = 1_000.0
GOAL_GLOBAL_USD = 1_000_000.0


def build_avenue_goal_progress(*, nte_store: Optional[MemoryStore] = None) -> Dict[str, Any]:
    ms = nte_store or MemoryStore()
    ms.ensure_defaults()
    goals = ms.load_json("goals_state.json")
    trades, _meta = load_federated_trades(nte_store=ms)
    roll = avenue_fairness_rollups(trades)["by_avenue"]
    avenues = []
    for av, row in sorted(roll.items()):
        tc = int(row.get("trade_count") or 0)
        pnl = float(row.get("net_pnl_usd") or 0.0)
        wins = int(row.get("wins") or 0)
        wr = (wins / tc) if tc else 0.0
        avenues.append(
            {
                "avenue": av,
                "goal_A_usd": GOAL_A_USD,
                "realized_pnl_usd": pnl,
                "trade_count": tc,
                "win_rate": round(wr, 4),
                "efficiency_pnl_per_trade": round(pnl / max(1, tc), 6),
                "representation_quality_score": row.get("representation_quality_score"),
            }
        )
    return {
        "schema": "avenue_goal_progress_v1",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "goals_state_excerpt": {k: goals.get(k) for k in ("goal_1k_60d", "goal_1k_week", "weekly_profit_usd") if k in goals},
        "avenues": avenues,
    }


def build_global_goal_progress(*, nte_store: Optional[MemoryStore] = None) -> Dict[str, Any]:
    ms = nte_store or MemoryStore()
    ms.ensure_defaults()
    goals = ms.load_json("goals_state.json")
    trades, meta = load_federated_trades(nte_store=ms)
    total_pnl = sum(float(t.get("net_pnl_usd") or t.get("net_pnl") or 0) for t in trades if isinstance(t, dict))
    return {
        "schema": "global_goal_progress_v1",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "target_usd": GOAL_GLOBAL_USD,
        "federated_realized_pnl_sum_usd": round(total_pnl, 4),
        "merged_trade_count": meta.get("merged_trade_count"),
        "goals_state": goals,
    }


def build_learning_log_stub(runtime_root: Path) -> Dict[str, Any]:
    """Placeholder learning aggregate — extend with databank learning hooks as needed."""
    return {
        "schema": "learning_log_v1",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runtime_root": str(runtime_root.resolve()),
        "note": "Append-only learning streams live under databank (e.g. research_learning_hooks.jsonl) and NTE memory.",
    }


def write_goal_progress_artifacts(runtime_root: Path, *, nte_store: Optional[MemoryStore] = None) -> Dict[str, Path]:
    runtime_root = runtime_root.resolve()
    d = runtime_root / "goal_proof"
    d.mkdir(parents=True, exist_ok=True)
    p1 = d / "avenue_goal_progress.json"
    p2 = d / "global_goal_progress.json"
    p3 = d / "learning_log.json"
    p1.write_text(json.dumps(build_avenue_goal_progress(nte_store=nte_store), indent=2, default=str), encoding="utf-8")
    p2.write_text(json.dumps(build_global_goal_progress(nte_store=nte_store), indent=2, default=str), encoding="utf-8")
    p3.write_text(json.dumps(build_learning_log_stub(runtime_root), indent=2, default=str), encoding="utf-8")
    return {"avenue_goal_progress": p1, "global_goal_progress": p2, "learning_log": p3}
