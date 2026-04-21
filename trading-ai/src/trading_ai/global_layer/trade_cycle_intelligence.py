"""
Post-trade intelligence for supervised / daemon cycles — classifications and tags (evidence-bound).

Writes ``data/control/trade_cycle_intelligence.json`` under runtime root (machine-usable for CEO + bots).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.orchestration.supervised_avenue_a_truth import load_supervised_log_records
from trading_ai.storage.storage_adapter import LocalStorageAdapter

_TRUTH_VERSION = "trade_cycle_intelligence_v1"
_REL_OUT = "data/control/trade_cycle_intelligence.json"


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _num(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def classify_trade_efficiency(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    Classify a single trade row (supervised log or enriched record).

    Uses only fields present — does not invent PnL.
    """
    oc = str(row.get("outcome_class") or "")
    net = row.get("net_pnl_usd")
    if net is None:
        net = row.get("net_pnl")
    hold = row.get("hold_seconds") or row.get("duration_sec")
    pnl = _num(net, float("nan"))
    hold_s = _num(hold, float("nan"))

    if oc == "failed_pipeline":
        bucket = "loss_causing"
    elif oc == "partial_or_unproven":
        bucket = "inefficient"
    elif oc == "clean_full_proof":
        if pnl == pnl and pnl < 0:
            bucket = "loss_causing"
        elif pnl == pnl and pnl > 0 and hold_s == hold_s and hold_s > 3600:
            bucket = "acceptable"
        elif pnl == pnl and pnl > 0:
            bucket = "optimal"
        else:
            bucket = "acceptable"
    else:
        bucket = "inefficient"

    missed_profit_usd: Optional[float] = None
    avoidable_loss_usd: Optional[float] = None
    if pnl == pnl and pnl < 0:
        avoidable_loss_usd = abs(pnl)

    tags: List[str] = []
    if oc == "clean_full_proof":
        tags.append("truth_chain_complete")
    if hold_s == hold_s and hold_s < 120:
        tags.append("short_hold")
    spread = row.get("spread_bps_estimate")
    if spread is not None:
        tags.append("spread_observed")

    return {
        "efficiency_class": bucket,
        "expected_vs_actual_note": "actual_from_record_fields_only",
        "missed_profit_usd": missed_profit_usd,
        "avoidable_loss_usd": avoidable_loss_usd,
        "micro_tags": tags,
    }


def refresh_trade_cycle_intelligence_bundle(runtime_root: Path) -> Dict[str, Any]:
    """Recompute structured analysis for all supervised log lines."""
    root = Path(runtime_root).resolve()
    raw = load_supervised_log_records(root)
    analyzed: List[Dict[str, Any]] = []
    for r in raw:
        cls = classify_trade_efficiency(r)
        analyzed.append(
            {
                "source_record": r,
                "analysis": cls,
            }
        )

    strat: Dict[str, Dict[str, Any]] = {}
    for row in analyzed:
        sid = str(row["source_record"].get("strategy_id") or row["source_record"].get("strategy") or "unknown")
        agg = strat.setdefault(sid, {"n": 0, "clean": 0, "failed": 0})
        agg["n"] += 1
        oc = str(row["source_record"].get("outcome_class") or "")
        if oc == "clean_full_proof":
            agg["clean"] += 1
        if oc == "failed_pipeline":
            agg["failed"] += 1

    payload = {
        "truth_version": _TRUTH_VERSION,
        "generated_at": _iso(),
        "runtime_root": str(root),
        "trade_count": len(raw),
        "analyzed_trades": analyzed,
        "per_strategy_rollup": strat,
        "honesty": "PnL fields may be absent in supervised rows — classifications degrade to outcome_class-only.",
    }
    ad = LocalStorageAdapter(runtime_root=root)
    ad.write_json(_REL_OUT, payload)
    return payload
