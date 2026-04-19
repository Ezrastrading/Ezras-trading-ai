"""Deep anomaly packet — only when exception review or manual trigger."""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

from trading_ai.global_layer.internal_data_reader import read_normalized_internal


def expand_packet_for_anomaly(
    base_packet: Dict[str, Any],
    *,
    trade_tail: int = 15,
) -> Dict[str, Any]:
    """Attach recent trade subset + anomaly hints; keep bounded."""
    internal = read_normalized_internal()
    trades: List[Dict[str, Any]] = [t for t in (internal.get("trades") or []) if isinstance(t, dict)]
    tail = copy.deepcopy(trades[-trade_tail:])
    out = dict(base_packet)
    out["anomaly_expansion"] = {
        "recent_trade_subset": tail,
        "hard_stop_trace": base_packet.get("risk_summary"),
        "packet_note": "anomaly_expansion_v1",
    }
    return out
