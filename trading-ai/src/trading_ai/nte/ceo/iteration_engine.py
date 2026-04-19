"""DO → OBSERVE → LEARN → ADJUST — after each trade and each day."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict


class IterationEngine:
    def __init__(self, store: Any) -> None:
        self.store = store

    def after_trade(self, summary: Dict[str, Any]) -> None:
        log = self.store.load_json("iteration_log.json")
        ev = log.get("events") or []
        if not isinstance(ev, list):
            ev = []
        ev.append(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "type": "trade",
                "data": summary,
            }
        )
        log["events"] = ev[-2000:]
        self.store.save_json("iteration_log.json", log)

    def after_day(self, day_summary: Dict[str, Any]) -> None:
        rev = self.store.load_json("review_state.json")
        rev["last_daily"] = day_summary
        try:
            import time as _time

            from trading_ai.nte.ceo.action_tracker import list_open_actions
            from trading_ai.nte.ceo.followup import metric_baseline

            rev["ceo_followup_snapshot"] = {
                "ts": _time.time(),
                "open_actions": len(list_open_actions()),
                "metrics": metric_baseline(),
            }
        except Exception:
            pass
        self.store.save_json("review_state.json", rev)
        self.after_trade({"kind": "day_close", **day_summary})
