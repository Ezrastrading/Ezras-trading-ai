"""Read/write AI review artifacts under ``global/memory`` — JSON + JSONL history."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.global_layer.global_memory_store import GlobalMemoryStore, _iso

logger = logging.getLogger(__name__)


def _default_queue_entry_template() -> Dict[str, Any]:
    return {
        "schema_version": "1.0",
        "generated_at": _iso(),
        "items": [],
    }


def _default_governance_events() -> Dict[str, Any]:
    return {"schema_version": "1.0", "generated_at": _iso(), "events": []}


def _default_scheduler_state() -> Dict[str, Any]:
    return {
        "schema_version": "1.0",
        "generated_at": _iso(),
        "last_morning_ts": None,
        "last_midday_ts": None,
        "last_eod_ts": None,
        "last_exception_ts": None,
        "last_packet_id": None,
        "last_joint_review_id": None,
        "suppress_midday": False,
        "suppress_all": False,
        "layer_degraded": False,
        "layer_degraded_reason": None,
    }


def _default_policy_snapshot() -> Dict[str, Any]:
    return {
        "schema_version": "1.0",
        "generated_at": _iso(),
        "snapshot": {},
    }


def _default_ceo_capital() -> Dict[str, Any]:
    return {
        "schema_version": "1.0",
        "generated_at": _iso(),
        "capital_mission_state": {},
        "best_live_edges": {},
        "search_and_discovery": {},
        "safety_and_governance": {},
        "recommendations": {},
        "first_million_path": {},
    }


def _default_first_million_progress() -> Dict[str, Any]:
    return {
        "schema_version": "1.0",
        "generated_at": _iso(),
        "progress_pct": 0.0,
        "velocity_label": "weak",
        "bottleneck": "",
        "main_opportunity": "",
    }


def _empty_latest(name: str) -> Dict[str, Any]:
    return {"schema_version": "1.0", "generated_at": _iso(), "artifact": name, "empty": True}


REVIEW_JSON_DEFAULTS = {
    "candidate_queue.json": _default_queue_entry_template,
    "promotion_queue.json": _default_queue_entry_template,
    "risk_reduction_queue.json": _default_queue_entry_template,
    "ceo_review_queue.json": _default_queue_entry_template,
    "speed_to_goal_review.json": lambda: {
        "schema_version": "1.0",
        "generated_at": _iso(),
        "summary": "",
        "accelerators": [],
        "drag_factors": [],
    },
    "governance_events.json": _default_governance_events,
    "review_scheduler_state.json": _default_scheduler_state,
    "review_policy_snapshot.json": _default_policy_snapshot,
    "ceo_capital_review.json": _default_ceo_capital,
    "first_million_progress_review.json": _default_first_million_progress,
    "review_packet_latest.json": lambda: _empty_latest("packet"),
    "claude_review_latest.json": lambda: _empty_latest("claude"),
    "gpt_review_latest.json": lambda: _empty_latest("gpt"),
    "joint_review_latest.json": lambda: _empty_latest("joint"),
}


class ReviewStorage:
    """Review-layer persistence; uses :class:`GlobalMemoryStore` root."""

    JSONL_FILES = (
        "review_packet_history.jsonl",
        "claude_review_history.jsonl",
        "gpt_review_history.jsonl",
        "joint_review_history.jsonl",
        "review_action_log.jsonl",
        "review_anomaly_packets.jsonl",
    )

    def __init__(self, store: Optional[GlobalMemoryStore] = None) -> None:
        self.store = store or GlobalMemoryStore()

    def ensure_review_files(self) -> None:
        self.store.ensure_all()
        for name, factory in REVIEW_JSON_DEFAULTS.items():
            p = self.store.path(name)
            if not p.exists():
                self.store._write_json(p, factory())  # noqa: SLF001
        for jf in self.JSONL_FILES:
            p = self.store.path(jf)
            if not p.exists():
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("", encoding="utf-8")

    def load_json(self, name: str) -> Dict[str, Any]:
        self.ensure_review_files()
        p = self.store.path(name)
        try:
            if p.is_file():
                raw = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    return raw
        except Exception as exc:
            logger.warning("review_storage load %s: %s", name, exc)
        fac = REVIEW_JSON_DEFAULTS.get(name)
        if fac:
            d = fac()
            self.store._write_json(p, d)  # noqa: SLF001
            return d
        return {}

    def save_json(self, name: str, data: Dict[str, Any]) -> None:
        self.ensure_review_files()
        data = dict(data)
        data.setdefault("schema_version", "1.0")
        data["generated_at"] = _iso()
        self.store._write_json(self.store.path(name), data)  # noqa: SLF001

    def append_jsonl(self, name: str, record: Dict[str, Any]) -> None:
        self.ensure_review_files()
        p = self.store.path(name)
        line = json.dumps(record, default=str) + "\n"
        with p.open("a", encoding="utf-8") as f:
            f.write(line)

