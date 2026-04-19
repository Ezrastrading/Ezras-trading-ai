"""Goal A/B/C — $1K/60d, $1K/week, $2K/week; writes goals_state.json."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from trading_ai.nte.nte_global.capital_ledger import snapshot_for_goals


class GoalEvaluator:
    def __init__(self, store: Any) -> None:
        self.store = store

    def update(
        self,
        *,
        equity: float,
        weekly_net_profit_usd: Optional[float] = None,
    ) -> Dict[str, Any]:
        snap = snapshot_for_goals()
        eq = float(snap.get("equity_estimate") or equity)
        wk = weekly_net_profit_usd
        if wk is None:
            wk = float(snap.get("weekly_net_profit_usd") or 0.0)

        g = self.store.load_json("goals_state.json")
        now = time.time()
        if g.get("start_ts") is None:
            g["start_ts"] = now
            g["start_equity"] = float(eq)
        g["last_equity"] = float(eq)
        g["ledger_snapshot_ts"] = now
        g["from_ledger"] = {
            "weekly_net_profit_usd": wk,
            "capital_added": float(snap.get("capital_added") or 0.0),
            "realized_pnl_net": float(snap.get("realized_pnl_net") or 0.0),
        }

        start_eq = float(g.get("start_equity") or eq)
        days = (now - float(g.get("start_ts") or now)) / 86400.0

        g1 = g.get("goal_1k_60d") or {}
        met_1k_60 = float(eq) >= float(g1.get("target_usd") or 1000.0) and days <= float(
            g1.get("deadline_days") or 60
        )
        g1["met"] = bool(met_1k_60)

        if wk is not None:
            g["weekly_profit_usd"] = float(wk)
            gw = g.get("goal_1k_week") or {}
            gw["met"] = float(wk) >= float(gw.get("target_net_profit_usd") or 1000.0)
            g["goal_1k_week"] = gw
            g2 = g.get("goal_2k_week") or {}
            g2["met"] = float(wk) >= float(g2.get("target_net_profit_usd") or 2000.0)
            g["goal_2k_week"] = g2

        actions: List[str] = []
        if float(eq) < 1000.0 and days < 60:
            actions.append("Focus edge quality and fee-aware sizing until equity reaches $1,000.")
        if not (g.get("goal_1k_week") or {}).get("met"):
            actions.append("Track weekly net after fees vs $1,000 target.")
        actions.append("Review reward_state and strategy_scores every 20 trades.")
        g["top_3_actions"] = actions[:3]
        g["on_track"] = bool(met_1k_60) or days < 30

        self.store.save_json("goals_state.json", g)
        return g
