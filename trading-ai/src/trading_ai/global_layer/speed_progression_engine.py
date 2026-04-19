"""Global speed progression — capital truth first, then goals, then contributions."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from trading_ai.global_layer.avenue_contribution_analyzer import contribution_summary
from trading_ai.global_layer.global_memory_store import GlobalMemoryStore
from trading_ai.global_layer.goal_evolution_engine import propose_post_c_goals
from trading_ai.global_layer.goal_speed_analyzer import (
    active_goal_label,
    identify_blockers,
    label_speed,
    projected_days_to_goal_a,
)
from trading_ai.global_layer.internal_data_reader import read_normalized_internal
from trading_ai.global_layer.pnl_aggregator import aggregate_from_trades, refresh_global_pnl_files
from trading_ai.global_layer.progress_optimizer import build_acceleration_options
from trading_ai.global_layer.supabase_runtime_reader import read_supabase_snapshot


class SpeedProgressionEngine:
    def __init__(self, store: Optional[GlobalMemoryStore] = None) -> None:
        self.store = store or GlobalMemoryStore()

    def run_once(self) -> Dict[str, Any]:
        internal = read_normalized_internal()
        trades = internal["trades"]
        refresh_global_pnl_files(self.store, trades)
        agg = aggregate_from_trades(trades)
        rolling_7d = agg["rolling_7d_net_usd"]
        rolling_30d = agg["rolling_30d_net_usd"]

        equity = float(internal["capital_ledger"]["net_equity_estimate_usd"])
        gs = internal.get("goals_state") or {}
        st = gs.get("start_ts")
        days_live = 0
        if st is not None:
            try:
                days_live = max(0, int((time.time() - float(st)) / 86400))
            except (TypeError, ValueError):
                days_live = 0

        supa = read_supabase_snapshot()
        if supa.get("trades"):
            internal.setdefault("supabase_trades_count", len(supa["trades"]))

        active = active_goal_label(equity, rolling_7d)
        proj_days, conf = projected_days_to_goal_a(equity, trades)
        speed_lbl = label_speed(proj_days)
        blockers = identify_blockers(internal)
        contrib = contribution_summary(agg["by_avenue"])
        accel = build_acceleration_options(
            best_avenue=contrib["best_avenue"],
            worst_avenue=contrib["worst_avenue"],
        )

        safest = "Prioritize net-after-fees and strongest avenue allocation before sizing up."
        fastest = "Parallel path: fix top blocker, reallocate to best avenue, tighten execution."
        if proj_days == float("inf"):
            fastest = "Insufficient positive edge — research + sandbox before pushing size."

        proj_out = float(min(proj_days, 99999.0)) if proj_days != float("inf") else -1.0

        out = {
            "schema_version": "1.0",
            "active_goal": active,
            "current_status": {
                "current_equity": equity,
                "rolling_7d_net_profit": rolling_7d,
                "rolling_30d_net_profit": rolling_30d,
                "days_live": days_live,
            },
            "current_speed": {
                "projected_days_to_goal": proj_out,
                "confidence": conf,
                "progress_rate_label": speed_lbl,
            },
            "blockers": blockers,
            "acceleration_options": accel,
            "best_path": {
                "safest_path": safest,
                "fastest_realistic_path": fastest,
                "top_3_actions": [
                    "Reconcile capital ledger vs deposits (earned vs funded)",
                    f"Lean into {contrib['best_avenue'] or 'best avenue'} if evidence holds",
                    "Cut or sandbox weakest strategies first",
                ],
            },
            "strongest_avenue": contrib["best_avenue"],
            "weakest_avenue": contrib["worst_avenue"],
            "supabase_notes": supa.get("missing_sources", []),
        }
        self.store.save_json("speed_progression.json", out)

        path = self.store.load_json("progress_path.json")
        gp = path.setdefault("goal_path", [])
        block_names = [b["name"] for b in blockers[:3]]
        accel_names = [a["name"] for a in accel[:3]]
        primary_av = list((agg.get("by_avenue") or {}).keys())[:5]
        if gp and gp[-1].get("goal_id") == active and gp[-1].get("status") == "active":
            gp[-1]["top_blockers"] = block_names
            gp[-1]["top_accelerators"] = accel_names
            gp[-1]["primary_avenues"] = primary_av
        else:
            gp.append(
                {
                    "goal_id": active,
                    "status": "active",
                    "started_at": gs.get("start_ts"),
                    "projected_completion_at": None,
                    "primary_avenues": primary_av,
                    "primary_strategies": [],
                    "top_blockers": block_names,
                    "top_accelerators": accel_names,
                }
            )
        path["goal_path"] = gp[-90:]
        self.store.save_json("progress_path.json", path)

        if active == "POST_C":
            post = propose_post_c_goals(
                rolling_7d=rolling_7d,
                rolling_30d=rolling_30d,
                avenue_mix=agg["by_avenue"],
            )
            gg = self.store.load_json("generated_goals.json")
            gg["post_goal_c_candidates"] = post
            self.store.save_json("generated_goals.json", gg)

        return out
