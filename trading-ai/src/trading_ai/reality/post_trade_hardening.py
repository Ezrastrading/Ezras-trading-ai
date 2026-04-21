"""Post-close hooks: execution truth, fill quality, snapshot, overtrading state (measurement only)."""

from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional

logger = logging.getLogger(__name__)


def run_post_trade_reality_hardening(merged: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Invoked from :meth:`OrganismClosedTradeHook.after_closed_trade` with the validated databank row.
    Returns a small dict of sub-stages for logging (never raises).
    """
    out: Dict[str, Any] = {}
    ex_rec: Optional[Dict[str, Any]] = None
    fill_ev: Optional[Dict[str, Any]] = None

    try:
        from trading_ai.reality.execution_truth import append_execution_truth_from_databank_trade

        ex = append_execution_truth_from_databank_trade(merged)
        if ex is not None:
            ex_rec = ex.to_dict()
        out["execution_truth"] = bool(ex_rec)
    except Exception as exc:
        logger.warning("post_trade execution_truth: %s", exc)
        out["execution_truth"] = False

    try:
        from trading_ai.monitoring.fill_quality import append_fill_quality_log, evaluate_fill_quality

        fill_ev = evaluate_fill_quality(merged)
        out["fill_quality"] = append_fill_quality_log(merged, evaluation=fill_ev)
    except Exception as exc:
        logger.warning("post_trade fill_quality: %s", exc)

    try:
        from trading_ai.nte.databank.local_trade_store import load_all_trade_events
        from trading_ai.control.reality_snapshot import update_reality_snapshot_after_trade
        from trading_ai.intelligence.overtrading_guard import refresh_overtrading_after_close

        events = load_all_trade_events()
        refresh_overtrading_after_close(events)
        update_reality_snapshot_after_trade(
            merged,
            events=events,
            execution_record=ex_rec,
            fill_eval=fill_ev,
        )
        out["reality_snapshot"] = True
        out["overtrading_refresh"] = True
    except Exception as exc:
        logger.warning("post_trade snapshot/overtrading: %s", exc)

    return out
