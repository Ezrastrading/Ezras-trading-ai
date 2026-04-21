"""Structured learning log — append-only JSONL; no silent live policy changes."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.learning.paths import system_learning_log_path
from trading_ai.runtime_paths import ezras_runtime_root

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_learning_entry(
    entry: Dict[str, Any],
    *,
    runtime_root: Optional[Path] = None,
) -> Path:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    p = root / "data" / "learning" / "system_learning_log.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "timestamp": entry.get("timestamp") or _now_iso(),
        "event_type": entry.get("event_type") or "unknown",
        "what_happened": entry.get("what_happened") or "",
        "why_it_happened": entry.get("why_it_happened") or "",
        "confidence": str(entry.get("confidence") or "unknown"),
        "improvement_suggestion": entry.get("improvement_suggestion") or "",
        "requires_ceo_review": bool(entry.get("requires_ceo_review", False)),
    }
    line = json.dumps(row, default=str) + "\n"
    with p.open("a", encoding="utf-8") as f:
        f.write(line)
    return p


def _infer_why_and_assumption(event_type: str, context: Dict[str, Any]) -> tuple[str, str, str, bool]:
    """Return (why, suggestion, confidence, needs_ceo)."""
    if event_type in ("trade_open", "placed"):
        tid = context.get("trade_id") or context.get("id")
        return (
            f"Trade opened id={tid}; outlet={context.get('outlet') or context.get('platform')}",
            "Verify sizing vs reserve policy and venue min notional before scaling.",
            "medium",
            False,
        )
    if event_type in ("trade_close", "closed"):
        res = context.get("result") or context.get("outcome")
        pnl = context.get("net_pnl_usd") or context.get("payout_dollars")
        why = f"Trade closed result={res} pnl={pnl}"
        sug = (
            "If loss: check whether exit was regime mismatch vs bad luck — review edge registry."
            if str(res).lower() == "loss"
            else "If win: confirm edge vs luck via sample size before increasing size."
        )
        return why, sug, "medium", str(res).lower() == "loss"
    if event_type == "validation":
        return (
            "Validation / preflight cycle ran.",
            "If blocked: capture root cause code (policy, balance, min notional) in next CEO review.",
            "high",
            bool(context.get("blocked") or context.get("failure")),
        )
    if event_type == "blocked_trade":
        return (
            f"Trade blocked: {context.get('reason') or context.get('detail')}",
            "Align runtime policy, quote balance, and venue catalog before retry.",
            "high",
            True,
        )
    if event_type == "failure":
        return (
            f"Failure: {context.get('error') or context.get('message')}",
            "Escalate to operator; do not auto-change guards or ratios.",
            "low",
            True,
        )
    if event_type == "readiness":
        return (
            "Readiness artifact refresh.",
            "Cross-check Gate A vs Gate B scope; Gate B is never implied by Gate A readiness.",
            "high",
            bool((context.get("critical_blockers") or [])),
        )
    if event_type == "daily_cycle":
        return ("Daily operator / bundle cycle.", "Review ratio and learning summaries.", "medium", False)
    return (
        f"Event {event_type}",
        "Continue measurement; no silent execution changes.",
        "low",
        False,
    )


def run_self_learning_engine(
    event_type: str,
    context: Optional[Dict[str, Any]] = None,
    *,
    runtime_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    After important events: append one structured learning row (idempotent at log level —
    callers should not duplicate the same second-level event).
    """
    ctx = dict(context or {})
    why, sug, conf, ceo = _infer_why_and_assumption(event_type, ctx)
    what = ctx.get("what_happened") or json.dumps(
        {k: ctx[k] for k in list(ctx.keys())[:24]},
        default=str,
    )[:4000]
    p = append_learning_entry(
        {
            "event_type": event_type,
            "what_happened": what,
            "why_it_happened": why,
            "confidence": conf,
            "improvement_suggestion": sug,
            "requires_ceo_review": ceo,
        },
        runtime_root=runtime_root,
    )
    try:
        from trading_ai.learning.self_learning_memory import touch_memory_after_event

        touch_memory_after_event(event_type, runtime_root=runtime_root)
    except Exception as exc:
        logger.debug("touch_memory_after_event: %s", exc)
    return {"status": "ok", "path": str(p), "event_type": event_type}


def build_derived_execution_reasoning(
    *,
    product_id: str,
    strategy_route: str,
    regime: str,
    spread_pct: Optional[float],
    edge_detail: str,
) -> Dict[str, Any]:
    """Transparent pre-action reasoning from available signals (no LLM)."""
    sp = spread_pct if spread_pct is not None else None
    return {
        "decision_reasoning": (
            f"NTE entry candidate on {product_id} via route={strategy_route} in regime={regime}; "
            f"spread_pct={sp}"
        ),
        "expected_outcome": (
            "Positive net edge after fees if regime and liquidity match edge registry assumptions; "
            "otherwise flat or small loss within risk caps."
        ),
        "risk_assessment": (
            f"Execution subject to governance + strategy firewall + edge scale; detail: {edge_detail}"
        ),
        "confidence_level": "derived_from_signals_not_llm",
    }


def run_daily_learning_if_needed(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """Delegates to self_learning_review (single import surface for hooks)."""
    from trading_ai.learning.self_learning_review import run_daily_learning_if_needed as _daily

    return _daily(runtime_root=runtime_root)
