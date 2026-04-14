"""Doctrine gate — mandatory pre-trade compliance. No time-of-day windows. Ever."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

MARGIN_DOCTRINE: Dict[str, Any] = {
    "never_borrow_for_low_confidence": True,
    "never_borrow_during_drawdown": True,
    "max_simultaneous_margin_positions": 1,
    "margin_requires_tier_a_or_b": True,
    "phase_1_max_margin_pct": 0.20,
    "phase_3_plus_max_margin_pct": 0.10,
    "phase_3_plus_high_conf_only": True,
}


@dataclass
class DoctrineContext:
    """Inputs required to evaluate whether a trade may proceed."""

    source: str
    mandate_compounding_paused: bool = False
    mandate_gaps_paused: bool = False
    execution_paused: bool = False
    edge_after_fees: float = 0.0
    min_edge_for_phase: float = 0.0
    anti_forced_trade: bool = True
    cluster_paused: bool = False
    tags: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DoctrineResult:
    ok: bool
    reason: str
    audit: Dict[str, Any] = field(default_factory=dict)


def is_execution_paused() -> bool:
    """
    Execution pause only when persisted capital drawdown exceeds 40% or ``manual_pause`` is set.

    Ignores in-memory ``MANDATE.execution_paused`` so stale flags cannot block at 0% drawdown.
    """
    try:
        from trading_ai.shark.state_store import load_capital, load_execution_control
    except Exception:
        return False
    cap = load_capital()
    if cap.current_capital <= 0:
        return False
    peak = cap.peak_capital
    if peak <= 0:
        return False
    drawdown = (peak - cap.current_capital) / peak
    if drawdown > 0.40:
        return True
    state = load_execution_control()
    return bool(state.get("manual_pause", False))


def check_doctrine_gate(ctx: DoctrineContext) -> DoctrineResult:
    """
    Hard gate. No monthly targets, idle timers, or clocks may force a trade.
    Drawdown >25% is handled via sizing (execution chain), not this gate.
    Drawdown >40% pauses execution (see ``is_execution_paused``) or ``manual_pause`` in execution_control.json.
    """
    audit: Dict[str, Any] = {"source": ctx.source, "tags": dict(ctx.tags)}
    if is_execution_paused():
        return DoctrineResult(False, "doctrine: execution_paused", audit)
    is_compounding = ctx.source in ("shark_compounding",) or "compounding" in ctx.source
    is_gap = ctx.source in ("shark_gap",) or bool(ctx.tags.get("gap_exploit"))
    if is_compounding and ctx.mandate_compounding_paused:
        return DoctrineResult(False, "doctrine: mandate_compounding_paused", audit)
    if is_gap and ctx.mandate_gaps_paused:
        return DoctrineResult(False, "doctrine: mandate_gaps_paused", audit)
    if ctx.cluster_paused:
        return DoctrineResult(False, "doctrine: cluster_paused", audit)
    if ctx.anti_forced_trade and ctx.edge_after_fees < ctx.min_edge_for_phase:
        return DoctrineResult(
            False,
            f"doctrine: anti_forced_trade edge {ctx.edge_after_fees:.4f} < min {ctx.min_edge_for_phase:.4f}",
            audit,
        )
    return DoctrineResult(True, "doctrine: ok", audit)


def merge_audit(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    out.update(extra)
    return out


def audit_trail_append(entries: List[Dict[str, Any]], step: str, detail: Dict[str, Any]) -> None:
    entries.append({"step": step, **detail})
