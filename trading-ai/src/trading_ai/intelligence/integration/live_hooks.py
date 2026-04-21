"""
Single integration surface for execution outcomes → intelligence / tickets / candidate learning.

Never blocks trading; never claims venue truth without evidence_refs.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.shark.models import ExecutionIntent, OrderResult

logger = logging.getLogger(__name__)


def _gate_for_outlet(outlet: str) -> str:
    o = (outlet or "").lower()
    if o == "coinbase":
        return "gate_a"
    if o in ("kalshi",):
        return "gate_b"
    return ""


def _append_candidate_learning(event: Dict[str, Any], *, runtime_root: Optional[Path]) -> None:
    try:
        from trading_ai.runtime_paths import ezras_runtime_root

        root = runtime_root or Path(ezras_runtime_root())
        d = root / "data" / "learning"
        d.mkdir(parents=True, exist_ok=True)
        p = d / "candidate_learning_queue.jsonl"
        event.setdefault("ts", time.time())
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, default=str) + "\n")
    except Exception as exc:
        logger.debug("candidate_learning_queue append skipped: %s", exc)


def record_shark_submit_outcome(intent: ExecutionIntent, result: OrderResult) -> Dict[str, Any]:
    """
    Called once per :func:`submit_order` with the final ``OrderResult`` (blocked or venue).

    - Builds a normalized execution event with explicit proof / venue-reached semantics.
    - Runs ticket detection (heuristic); weak signals go to candidate queue only when appropriate.
    """
    outlet = str(intent.outlet or "")
    gate = _gate_for_outlet(outlet)
    meta = intent.meta if isinstance(intent.meta, dict) else {}
    market_id = str(intent.market_id or meta.get("product_id") or meta.get("symbol") or "")

    venue_reached = result.status not in (
        "intelligence_blocked",
        "governance_blocked",
        "strategy_blocked",
        "capital_blocked",
        "halted",
        "system_execution_lock",
        "disabled",
        "geo_blocked",
        "adaptive_os_blocked",
    ) and not str(result.reason or "").startswith("Live order blocked")

    ev: Dict[str, Any] = {
        "trigger": str(result.reason or result.status or "submit_outcome"),
        "avenue_id": outlet,
        "gate_id": gate,
        "venue": outlet,
        "market_id": market_id,
        "product_id": str(meta.get("product_id") or market_id),
        "source_component": "trading_ai.shark.execution_live:submit_order",
        "human_summary": f"Submit outcome status={result.status} success={result.success} outlet={outlet}",
        "machine_summary": json.dumps(
            {
                "status": result.status,
                "success": result.success,
                "order_id": result.order_id,
                "venue_reached": venue_reached,
            },
            default=str,
        ),
        "evidence_refs": [],
        "confidence": 0.45 if not venue_reached else 0.55,
        "heuristic_only": True,
        "requires_external_evidence": True,
        "market_truth_verified": False,
        "detector_classification": "execution_submit_outcome",
        "operator_actionability": "review_recommended" if not result.success else "informational",
        "extra": {
            "proof_tier": "runtime_submit_event",
            "venue_reached": venue_reached,
        },
    }

    tickets_created = 0
    append_tickets = (os.environ.get("EZRAS_INTELLIGENCE_HOOK_APPEND_TICKETS") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )
    try:
        from trading_ai.intelligence.tickets.detect import detect_from_execution_event
        from trading_ai.intelligence.tickets.store import append_ticket

        for t in detect_from_execution_event(ev):
            t.extra.setdefault("detector_honesty", {})
            t.extra["detector_honesty"].update(
                {
                    "detector_type": "execution_submit",
                    "heuristic_only": True,
                    "requires_external_evidence": True,
                    "evidence_refs_attached": bool(t.evidence_refs),
                    "market_truth_verified": False,
                }
            )
            if append_tickets:
                append_ticket(t)
            tickets_created += 1
    except Exception as exc:
        logger.debug("ticket append from submit_order skipped: %s", exc)

    _append_candidate_learning(
        {
            "kind": "submit_order_outcome",
            "event": ev,
            "tickets_created": tickets_created,
        },
        runtime_root=None,
    )

    return {"ok": True, "tickets_created": tickets_created, "venue_reached": venue_reached}


def record_post_trade_hub_event(
    phase: str,
    trade: Dict[str, Any],
    hub_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Lifecycle hook from post_trade_hub (placed/closed) — heuristic tickets + candidate queue only."""
    tid = str(trade.get("trade_id") or "")
    ev = {
        "trigger": f"post_trade_{phase}",
        "avenue_id": str(trade.get("avenue_id") or trade.get("outlet") or ""),
        "gate_id": str(trade.get("gate_id") or ""),
        "venue": str(trade.get("venue") or trade.get("outlet") or ""),
        "market_id": str(trade.get("market_id") or trade.get("market") or ""),
        "source_component": "trading_ai.automation.post_trade_hub",
        "human_summary": f"Post-trade {phase} for trade_id={tid or 'unknown'}",
        "machine_summary": json.dumps({"trade": trade, "hub": hub_result or {}}, default=str)[:8000],
        "evidence_refs": list(trade.get("evidence_refs") or []),
        "confidence": 0.4,
        "heuristic_only": True,
        "detector_classification": "post_trade_lifecycle",
    }
    n = 0
    try:
        from trading_ai.intelligence.tickets.detect import detect_from_execution_event
        from trading_ai.intelligence.tickets.store import append_ticket

        append_tickets = (os.environ.get("EZRAS_INTELLIGENCE_HOOK_APPEND_TICKETS") or "1").strip().lower() not in (
            "0",
            "false",
            "no",
        )
        for t in detect_from_execution_event(ev):
            if append_tickets:
                append_ticket(t)
            n += 1
    except Exception as exc:
        logger.debug("post_trade intelligence hook skipped: %s", exc)
    _append_candidate_learning({"kind": f"post_trade_{phase}", "trade_id": tid, "tickets": n}, runtime_root=None)
    return {"ok": True, "tickets_created": n}


def emit_gate_b_artifact_event(event_name: str, payload: Dict[str, Any]) -> None:
    """Gate B scan/artifact pipeline → candidate learning (no ticket spam by default)."""
    ev = {
        "trigger": event_name,
        "avenue_id": "A",
        "gate_id": "gate_b",
        "venue": "kalshi_or_spot_schema",
        "source_component": "gate_b_artifact_pipeline",
        "human_summary": f"Gate B artifact event: {event_name}",
        "machine_summary": json.dumps(payload, default=str)[:4000],
        "evidence_refs": [],
        "confidence": 0.35,
        "heuristic_only": True,
    }
    _append_candidate_learning({"kind": "gate_b_artifact", "event": ev}, runtime_root=None)
