"""Evaluate hard-stop rules for live Coinbase NTE (entries pause)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.nte.execution.state import load_state
from trading_ai.nte.paths import nte_system_health_path

logger = logging.getLogger(__name__)


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except Exception as exc:
        logger.debug("hard_stops read %s: %s", path, exc)
        return None


def evaluate_hard_stops(
    *,
    user_ws_stale: Optional[bool] = None,
    net_pnl_recent_usd: Optional[float] = None,
    consecutive_losses_override: Optional[int] = None,
) -> Tuple[bool, List[str]]:
    """
    Returns (should_stop_new_entries, reasons).

    When user_ws_stale is None, user-stream instability is not evaluated (not wired globally yet).
    """
    reasons: List[str] = []
    st = load_state()
    h = _read_json(nte_system_health_path()) or {}

    if user_ws_stale is True:
        reasons.append("user_ws_stale")

    dc = h.get("degraded_components") or []
    if isinstance(dc, list):
        if len(dc) >= 2:
            reasons.append("multiple_degraded_components")
        for tag in ("stale_market_data", "websocket"):
            if tag in dc:
                reasons.append(f"degraded_{tag}")
    if h.get("healthy") is False:
        reasons.append("system_unhealthy")

    cl = consecutive_losses_override
    if cl is None:
        cl = int(st.get("consecutive_losses") or 0)
    if cl >= 3:
        reasons.append(f"consecutive_losses_{cl}")

    if net_pnl_recent_usd is not None and net_pnl_recent_usd < -150.0:
        reasons.append("net_pnl_sharply_negative")

    reasons = list(dict.fromkeys(reasons))
    return (len(reasons) > 0, reasons)


def hard_stop_summary_block() -> str:
    stop, rs = evaluate_hard_stops()
    lines = ["**Hard-stop (new entries)**", f"- triggered: {stop}", "- reasons:"]
    for r in rs:
        lines.append(f"  - {r}")
    if not rs:
        lines.append("  - (none)")
    return "\n".join(lines)
