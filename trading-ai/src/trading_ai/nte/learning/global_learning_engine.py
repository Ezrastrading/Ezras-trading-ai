"""Global learning — every trade; batch review every 20 trades."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class GlobalLearningEngine:
    def __init__(self, store: Any) -> None:
        self.store = store

    def on_trade_closed(self, record: Dict[str, Any]) -> None:
        self.store.append_trade(record)
        tm = self.store.load_json("trade_memory.json")
        n = len(tm.get("trades") or [])
        # Execution intelligence refresh is owned by post_trade_closed (single writer + idempotent trade_id).
        if n > 0 and n % 20 == 0:
            self._analyze_batch()

    def _analyze_batch(self) -> None:
        tm = self.store.load_json("trade_memory.json")
        trades: List[Dict[str, Any]] = [t for t in (tm.get("trades") or []) if isinstance(t, dict)]
        recent = trades[-20:]
        if len(recent) < 5:
            return
        wins = [t for t in recent if float(t.get("net_pnl_usd") or 0) > 0]
        losses = [t for t in recent if float(t.get("net_pnl_usd") or 0) <= 0]
        by_setup: Dict[str, List[float]] = {}
        for t in recent:
            s = str(t.get("setup_type") or "unknown")
            by_setup.setdefault(s, []).append(float(t.get("net_pnl_usd") or 0.0))
        best = max(by_setup.items(), key=lambda kv: sum(kv[1]))[0] if by_setup else "n/a"
        worst = min(by_setup.items(), key=lambda kv: sum(kv[1]))[0] if by_setup else "n/a"
        body = (
            f"Last 20: wins={len(wins)} losses={len(losses)} | "
            f"best_setup={best} worst_setup={worst} | "
            f"edge_check: net=${sum(float(t.get('net_pnl_usd') or 0) for t in recent):.2f}"
        )
        self.store.append_md("lessons_log.md", "batch_20", body)
        thesis = self.store.path("master_thesis.md")
        try:
            with thesis.open("a", encoding="utf-8") as f:
                f.write(f"\n## batch @20\n\n{best} outperforming; watch {worst}.\n")
        except OSError as exc:
            logger.warning("master_thesis append: %s", exc)
