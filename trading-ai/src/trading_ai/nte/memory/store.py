"""Persistent JSON + markdown memory for NTE (all avenues share schema)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.nte.paths import nte_memory_dir

logger = logging.getLogger(__name__)


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_trade_memory() -> Dict[str, Any]:
    return {"trades": [], "updated": _iso()}


def _default_market_memory() -> Dict[str, Any]:
    return {
        "closes": {"BTC-USD": [], "ETH-USD": []},
        "max_len": 120,
        "updated": _iso(),
    }


def _default_strategy_scores() -> Dict[str, Any]:
    return {
        "avenues": {
            "coinbase": {
                "mean_reversion": {"score": 0.5, "trades": 0, "wins": 0},
                "continuation_pullback": {"score": 0.5, "trades": 0, "wins": 0},
                "micro_momentum": {"score": 0.5, "trades": 0, "wins": 0},
            }
        },
        "updated": _iso(),
    }


def _default_research_memory() -> Dict[str, Any]:
    return {
        "sandbox_strategies": [],
        "promoted": [],
        "rejected": [],
        "updated": _iso(),
    }


def _default_goals_state() -> Dict[str, Any]:
    return {
        "start_ts": None,
        "start_equity": None,
        "last_equity": None,
        "goal_1k_60d": {"target_usd": 1000.0, "deadline_days": 60, "met": None},
        "goal_1k_week": {"target_net_profit_usd": 1000.0, "met": None},
        "goal_2k_week": {"target_net_profit_usd": 2000.0, "met": None},
        "weekly_profit_usd": 0.0,
        "week_id": None,
        "on_track": None,
        "top_3_actions": [],
        "updated": _iso(),
    }


def _default_reward_state() -> Dict[str, Any]:
    return {
        "reward_score": 0.0,
        "penalty_score": 0.0,
        "discipline_score": 0.5,
        "strategy_scores": {},
        "streak_reward": 0,
        "streak_penalty": 0,
        "size_multiplier": 1.0,
        "updated": _iso(),
    }


def _default_iteration_log() -> Dict[str, Any]:
    return {"events": [], "updated": _iso()}


def _default_review_state() -> Dict[str, Any]:
    return {"last_daily": None, "last_weekly": None, "updated": _iso()}


class MemoryStore:
    """Maps spec file names → paths under ``shark/nte/memory``."""

    FILES = {
        "trade_memory.json": _default_trade_memory,
        "market_memory.json": _default_market_memory,
        "strategy_scores.json": _default_strategy_scores,
        "research_memory.json": _default_research_memory,
        "goals_state.json": _default_goals_state,
        "reward_state.json": _default_reward_state,
        "iteration_log.json": _default_iteration_log,
        "review_state.json": _default_review_state,
    }

    MD_FILES = ("lessons_log.md", "ceo_sessions.md", "master_thesis.md")

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = root or nte_memory_dir()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, name: str) -> Path:
        return self.root / name

    def ensure_defaults(self) -> None:
        for fname, factory in self.FILES.items():
            p = self.path(fname)
            if not p.exists():
                self._write_json(p, factory())
        for md in self.MD_FILES:
            p = self.path(md)
            if not p.exists():
                p.write_text(
                    f"# {md.replace('_', ' ').replace('.md', '').title()}\n\n"
                    f"Initialized {_iso()} UTC (NTE).\n",
                    encoding="utf-8",
                )

    def load_json(self, name: str) -> Dict[str, Any]:
        self.ensure_defaults()
        p = self.path(name)
        try:
            raw = p.read_text(encoding="utf-8")
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            logger.warning("memory load %s: %s — reset", name, exc)
            factory = self.FILES.get(name, _default_trade_memory)
            d = factory()
            self._write_json(p, d)
            return d

    def save_json(self, name: str, data: Dict[str, Any]) -> None:
        data = dict(data)
        data["updated"] = _iso()
        self._write_json(self.path(name), data)

    def _write_json(self, p: Path, data: Dict[str, Any]) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    def append_md(self, name: str, section: str, body: str) -> None:
        self.ensure_defaults()
        p = self.path(name)
        line = f"\n\n## {_iso()} — {section}\n\n{body.strip()}\n"
        with p.open("a", encoding="utf-8") as f:
            f.write(line)

    def append_trade(self, record: Dict[str, Any]) -> None:
        tm = self.load_json("trade_memory.json")
        trades: List[Any] = tm.get("trades") or []
        if not isinstance(trades, list):
            trades = []
        record = dict(record)
        record["logged_at"] = _iso()
        trades.append(record)
        tm["trades"] = trades[-5000:]
        self.save_json("trade_memory.json", tm)
