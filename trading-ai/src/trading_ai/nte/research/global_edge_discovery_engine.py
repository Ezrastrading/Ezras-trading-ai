"""Sandbox strategies — ROI, drawdown, consistency; promote only if > baseline."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class GlobalEdgeDiscoveryEngine:
    def __init__(self, store: Any) -> None:
        self.store = store

    def record_candidate(
        self,
        name: str,
        *,
        roi: float,
        max_dd: float,
        consistency: float,
        sandbox: bool = True,
    ) -> None:
        rm = self.store.load_json("research_memory.json")
        sand = rm.get("sandbox_strategies") or []
        if not isinstance(sand, list):
            sand = []
        sand.append(
            {
                "name": name,
                "roi": roi,
                "max_drawdown": max_dd,
                "consistency": consistency,
                "sandbox": sandbox,
            }
        )
        rm["sandbox_strategies"] = sand[-200:]
        self.store.save_json("research_memory.json", rm)

    def promote_if_stronger(self, name: str, baseline_roi: float) -> Optional[str]:
        rm = self.store.load_json("research_memory.json")
        sand = rm.get("sandbox_strategies") or []
        for row in reversed(sand if isinstance(sand, list) else []):
            if row.get("name") == name and float(row.get("roi") or 0) > baseline_roi:
                prom = rm.get("promoted") or []
                if not isinstance(prom, list):
                    prom = []
                prom.append(name)
                rm["promoted"] = prom[-100:]
                self.store.save_json("research_memory.json", rm)
                return name
        return None
