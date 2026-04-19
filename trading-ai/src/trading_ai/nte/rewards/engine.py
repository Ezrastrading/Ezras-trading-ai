"""Reward / punishment — influences size_multiplier and strategy weights."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class RewardEngine:
    def __init__(self, store: Any) -> None:
        self.store = store

    def load(self) -> Dict[str, Any]:
        return self.store.load_json("reward_state.json")

    def save(self, r: Dict[str, Any]) -> None:
        self.store.save_json("reward_state.json", r)

    def process_trade_outcome(
        self,
        *,
        net_pnl_usd: float,
        rule_adherent: bool,
        mistake: Optional[str],
        strategy: str,
    ) -> Dict[str, Any]:
        r = self.load()
        reward = float(r.get("reward_score") or 0.0)
        penalty = float(r.get("penalty_score") or 0.0)
        disc = float(r.get("discipline_score") or 0.5)
        sr = int(r.get("streak_reward") or 0)
        sp = int(r.get("streak_penalty") or 0)
        mult = float(r.get("size_multiplier") or 1.0)

        if net_pnl_usd > 0 and rule_adherent:
            reward += 1.0 + min(2.0, net_pnl_usd / 50.0)
            sr += 1
            sp = 0
            disc = min(1.0, disc + 0.02)
        elif net_pnl_usd > 0:
            reward += 0.3
            sr += 1
        else:
            penalty += 1.0 + min(2.0, abs(net_pnl_usd) / 50.0)
            sp += 1
            sr = 0
            disc = max(0.0, disc - 0.03)

        if mistake:
            penalty += 1.5
            sp += 1
            disc = max(0.0, disc - 0.05)

        if sr >= 3:
            mult = min(1.25, mult + 0.05)
        if sp >= 2:
            mult = max(0.35, mult - 0.08)

        r["reward_score"] = reward
        r["penalty_score"] = penalty
        r["discipline_score"] = disc
        r["streak_reward"] = sr
        r["streak_penalty"] = sp
        r["size_multiplier"] = mult
        self.save(r)
        self._bump_strategy_weight(strategy, net_pnl_usd > 0)
        return r

    def _bump_strategy_weight(self, strategy: str, won: bool) -> None:
        ss = self.store.load_json("strategy_scores.json")
        avenues = ss.get("avenues") or {}
        cb = avenues.get("coinbase") or {}
        if not isinstance(cb, dict):
            cb = {}
        row = cb.get(strategy)
        if not isinstance(row, dict):
            row = {"score": 0.5, "trades": 0, "wins": 0}
        row["trades"] = int(row.get("trades") or 0) + 1
        if won:
            row["wins"] = int(row.get("wins") or 0) + 1
        wr = row["wins"] / row["trades"] if row["trades"] else 0.5
        row["score"] = 0.35 + 0.65 * wr
        cb[strategy] = row
        avenues["coinbase"] = cb
        ss["avenues"] = avenues
        self.store.save_json("strategy_scores.json", ss)
