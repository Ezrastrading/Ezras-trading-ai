"""
Guaranteed Kalshi **execution** visibility for federated truth (not a substitute for closed-trade databank).

Every successful Kalshi ``submit_order`` appends one JSON line to ``kalshi_execution_mirror.jsonl``
under global memory. ``load_federated_trades`` ingests these as rows with
``truth_provenance.primary = kalshi_execution_mirror`` so the avenue is not silently absent when
only strategy modules wrote nothing to the databank.

This does **not** replace full ``process_closed_trade`` for PnL truth — it proves **activity** and supports
fairness; closes still need databank or memory rows for net PnL.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def append_kalshi_execution_mirror(
    *,
    intent_summary: Dict[str, Any],
    order_id: str,
    success: bool,
    raw_status: Optional[str] = None,
) -> None:
    if not success or not str(order_id or "").strip():
        return
    try:
        from trading_ai.global_layer.global_memory_store import GlobalMemoryStore

        st = GlobalMemoryStore()
        st.ensure_all()
        p = st.path("kalshi_execution_mirror.jsonl")
        row = {
            "mirror_id": f"kxm_{uuid.uuid4().hex[:16]}",
            "trade_id": f"kxm_{order_id}_{int(time.time())}",
            "avenue": "kalshi",
            "avenue_name": "kalshi",
            "kind": "execution_mirror",
            "order_id": order_id,
            "ts": time.time(),
            "intent": intent_summary,
            "status": raw_status or "submitted",
            "net_pnl_usd": None,
            "truth_note": "execution_mirror_only_not_a_close",
        }
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")
    except Exception as exc:
        logger.warning("kalshi_execution_mirror append failed: %s", exc)


def load_mirror_rows() -> list:
    try:
        from trading_ai.global_layer.global_memory_store import GlobalMemoryStore

        st = GlobalMemoryStore()
        st.ensure_all()
        p = st.path("kalshi_execution_mirror.jsonl")
        if not p.is_file():
            return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if isinstance(rec, dict):
                    out.append(rec)
            except json.JSONDecodeError:
                continue
        return out
    except Exception as exc:
        logger.debug("kalshi mirror load: %s", exc)
        return []
