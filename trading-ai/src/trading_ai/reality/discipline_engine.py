"""Discipline violations and score — cooldown when repeated breaks (measurement only)."""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from trading_ai.reality.paths import reality_data_dir


class ViolationKind(str, Enum):
    TRADE_WHEN_BLOCKED = "trade_taken_when_blocked"
    OVERRIDE_SAFETY = "override_of_safety_checks"
    OUTSIDE_REGIME = "trade_outside_regime"
    OVERSIZE = "oversize_trade"
    MANUAL_OVERRIDE = "manual_override_execution"


DEFAULT_PENALTIES: Dict[str, int] = {
    ViolationKind.TRADE_WHEN_BLOCKED.value: 25,
    ViolationKind.OVERRIDE_SAFETY.value: 20,
    ViolationKind.OUTSIDE_REGIME.value: 15,
    ViolationKind.OVERSIZE.value: 15,
    ViolationKind.MANUAL_OVERRIDE.value: 10,
}


@dataclass
class DisciplineResult:
    discipline_score: int
    violations: List[str]
    mark: str
    violation_events_last_20_trades: int
    cooldown_triggered: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "discipline_score": self.discipline_score,
            "violations": self.violations,
            "mark": self.mark,
            "violation_events_last_20_trades": self.violation_events_last_20_trades,
            "cooldown_triggered": self.cooldown_triggered,
        }


def discipline_log_path(path: Optional[Path] = None) -> Path:
    return (path or reality_data_dir()) / "discipline_log.jsonl"


class DisciplineEngine:
    """
    Per-trade discipline score (starts at 100) with penalties; tracks global violation
    events in the last 20 trades for cooldown (>= 3 events triggers cooldown).
    """

    def __init__(
        self,
        *,
        data_dir: Optional[Path] = None,
        penalties: Optional[Dict[str, int]] = None,
        recent_trade_window: int = 20,
        cooldown_event_threshold: int = 3,
    ) -> None:
        self._dir = data_dir or reality_data_dir()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._penalties = dict(penalties or DEFAULT_PENALTIES)
        self._recent = deque(maxlen=recent_trade_window)
        self._cooldown_threshold = int(cooldown_event_threshold)
        self._load_recent()

    def _load_recent(self) -> None:
        p = discipline_log_path(self._dir)
        if not p.is_file():
            return
        lines = [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        cap = getattr(self._recent, "maxlen", None) or 20
        for line in lines[-cap:]:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ev = int(rec.get("violation_event_count") or 0)
            self._recent.append(ev)

    def evaluate(
        self,
        violations: Sequence[str],
        *,
        trade_id: Optional[str] = None,
    ) -> DisciplineResult:
        score = 100
        vlist = [str(v) for v in violations if str(v).strip()]
        for v in vlist:
            score -= int(self._penalties.get(v, 10))
        score = max(0, min(100, score))
        mark = "DISCIPLINE_BREAK" if score < 80 else "OK"
        event_count = len(vlist)
        self._recent.append(event_count)
        events_last_20 = int(sum(self._recent))
        cooldown = events_last_20 >= self._cooldown_threshold
        result = DisciplineResult(
            discipline_score=score,
            violations=vlist,
            mark=mark,
            violation_events_last_20_trades=events_last_20,
            cooldown_triggered=cooldown,
        )
        rec = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "trade_id": trade_id,
            "violations": vlist,
            "discipline_score": score,
            "mark": mark,
            "violation_event_count": event_count,
            "violation_events_last_20_trades": events_last_20,
            "cooldown_triggered": cooldown,
        }
        lp = discipline_log_path(self._dir)
        lp.parent.mkdir(parents=True, exist_ok=True)
        with lp.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        return result
