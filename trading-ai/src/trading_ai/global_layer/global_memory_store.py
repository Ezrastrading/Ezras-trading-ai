"""Create and persist /memory/global/*.json — schemas v1.0."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.governance.storage_architecture import global_avenue_knowledge_dir, global_memory_dir

logger = logging.getLogger(__name__)


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_speed_progression() -> Dict[str, Any]:
    return {
        "schema_version": "1.0",
        "generated_at": _iso(),
        "active_goal": "A",
        "current_status": {
            "current_equity": 0.0,
            "rolling_7d_net_profit": 0.0,
            "rolling_30d_net_profit": 0.0,
            "days_live": 0,
        },
        "current_speed": {
            "projected_days_to_goal": 0.0,
            "confidence": 0.0,
            "progress_rate_label": "base",
        },
        "blockers": [],
        "acceleration_options": [],
        "best_path": {
            "safest_path": "",
            "fastest_realistic_path": "",
            "top_3_actions": [],
        },
    }


def _default_progress_path() -> Dict[str, Any]:
    return {
        "schema_version": "1.0",
        "generated_at": _iso(),
        "goal_path": [],
    }


def _default_generated_goals() -> Dict[str, Any]:
    return {
        "schema_version": "1.0",
        "generated_at": _iso(),
        "post_goal_c_candidates": [],
    }


def _default_pnl_bucket() -> Dict[str, Any]:
    return {
        "schema_version": "1.0",
        "generated_at": _iso(),
        "period_net_usd": 0.0,
        "by_avenue": {},
        "trade_count": 0,
        "notes": "",
    }


def _default_knowledge_base() -> Dict[str, Any]:
    return {
        "schema_version": "1.0",
        "generated_at": _iso(),
        "global_truths": [],
        "avenue_specific_truths": {
            "coinbase": [],
            "kalshi": [],
            "stocks": [],
            "options": [],
            "futures": [],
        },
        "confirmed_patterns": [],
        "rejected_patterns": [],
        "open_questions": [],
    }


def _default_market_knowledge() -> Dict[str, Any]:
    return {
        "schema_version": "1.0",
        "generated_at": _iso(),
        "avenues": {
            "coinbase": {
                "assets": {},
                "timing_patterns": [],
                "regime_patterns": [],
                "execution_patterns": [],
                "edge_patterns": [],
            }
        },
    }


def _default_source_rankings() -> Dict[str, Any]:
    return {"schema_version": "1.0", "generated_at": _iso(), "sources": []}


def _default_strategy_intelligence() -> Dict[str, Any]:
    return {"schema_version": "1.0", "generated_at": _iso(), "strategy_families": []}


def _default_knowledge_deltas() -> Dict[str, Any]:
    return {"schema_version": "1.0", "generated_at": _iso(), "deltas": []}


def _default_rejected_ideas() -> Dict[str, Any]:
    return {"schema_version": "1.0", "generated_at": _iso(), "rejected": []}


def _default_avenue_knowledge(avenue_id: str) -> Dict[str, Any]:
    return {
        "schema_version": "1.0",
        "generated_at": _iso(),
        "avenue_id": avenue_id,
        "internal_findings": [],
        "execution_findings": [],
        "timing_findings": [],
        "market_behavior_findings": [],
        "best_known_conditions": [],
        "worst_known_conditions": [],
        "research_candidates": [],
        "rejected_ideas": [],
    }


FILE_DEFAULTS = {
    "speed_progression.json": _default_speed_progression,
    "progress_path.json": _default_progress_path,
    "generated_goals.json": _default_generated_goals,
    "daily_pnl_summary.json": _default_pnl_bucket,
    "weekly_pnl_summary.json": _default_pnl_bucket,
    "monthly_pnl_summary.json": _default_pnl_bucket,
    "knowledge_base.json": _default_knowledge_base,
    "market_knowledge.json": _default_market_knowledge,
    "source_rankings.json": _default_source_rankings,
    "strategy_intelligence.json": _default_strategy_intelligence,
    "knowledge_deltas.json": _default_knowledge_deltas,
    "rejected_strategy_ideas.json": _default_rejected_ideas,
}

MD_FILES = (
    "external_research_briefs.md",
    "briefing_log.md",
)


class GlobalMemoryStore:
    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = root or global_memory_dir()

    def path(self, name: str) -> Path:
        return self.root / name

    def ensure_all(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for fname, factory in FILE_DEFAULTS.items():
            p = self.path(fname)
            if not p.exists():
                self._write_json(p, factory())
        for md in MD_FILES:
            p = self.path(md)
            if not p.exists():
                p.write_text(f"# {md.replace('_', ' ').title()}\n\nInitialized {_iso()} UTC.\n", encoding="utf-8")
        ak = global_avenue_knowledge_dir()
        for aid in ("coinbase", "kalshi", "stocks", "options", "futures"):
            p = ak / f"{aid}_knowledge.json"
            if not p.exists():
                self._write_json(p, _default_avenue_knowledge(aid))

    def load_json(self, name: str) -> Dict[str, Any]:
        self.ensure_all()
        p = self.path(name)
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception as exc:
            logger.warning("global memory load %s: %s", name, exc)
            fac = FILE_DEFAULTS.get(name)
            if fac:
                d = fac()
                self._write_json(p, d)
                return d
            return {}

    def save_json(self, name: str, data: Dict[str, Any]) -> None:
        data = dict(data)
        data["generated_at"] = _iso()
        if "schema_version" not in data:
            data["schema_version"] = "1.0"
        self._write_json(self.path(name), data)

    def append_md(self, name: str, body: str) -> None:
        self.ensure_all()
        p = self.path(name)
        with p.open("a", encoding="utf-8") as f:
            f.write(f"\n\n---\n\n{_iso()}\n\n{body.strip()}\n")

    def _write_json(self, p: Path, data: Dict[str, Any]) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
