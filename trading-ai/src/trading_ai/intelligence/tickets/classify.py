"""Map raw signals / keywords to ticket type and severity."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from trading_ai.intelligence.tickets.models import TicketSeverity, TicketType


def classify_signal(
    *,
    trigger: str,
    source_component: str = "",
    context: Optional[Dict[str, Any]] = None,
) -> Tuple[TicketType, TicketSeverity, str]:
    """
    Heuristic classifier — evidence should still be attached via ``evidence_refs``.
    Returns (ticket_type, severity, category_label).
    """
    ctx = context or {}
    t = (trigger or "").lower()
    sc = (source_component or "").lower()

    if "product_not_allowed" in t or ctx.get("reason") == "product_not_allowed":
        return TicketType.runtime_policy_mismatch, TicketSeverity.high, "policy_product"
    if "venue_min_notional_not_fundable" in t or ctx.get("reason") == "venue_min_notional_not_fundable":
        return TicketType.reserve_problem, TicketSeverity.medium, "reserve_notional"
    if "runtime_policy_disallows_fundable_product" in t:
        return TicketType.runtime_policy_mismatch, TicketSeverity.high, "policy_fundable"
    if "rejected" in t or ctx.get("order_status") == "rejected":
        return TicketType.execution_incident, TicketSeverity.medium, "execution_reject"
    if "partial" in t and "fill" in t:
        return TicketType.partial_fill_incident, TicketSeverity.medium, "execution_partial"
    if "timeout" in t or ctx.get("timeout"):
        return TicketType.timeout_incident, TicketSeverity.medium, "execution_timeout"
    if "fee_drag" in t or "fee-drag" in t or "fee drag" in t:
        return TicketType.strategy_degradation, TicketSeverity.medium, "fee_drag"
    if "fee_flip" in t or "flipped_negative_by_fees" in t or "net_flip" in t:
        return TicketType.strategy_degradation, TicketSeverity.medium, "fee_flip"
    if "slippage" in t:
        return TicketType.slippage_warning, TicketSeverity.medium, "execution_slippage"
    if "spread" in t or ctx.get("spread_pct", 0) > 0.02:
        return TicketType.liquidity_warning, TicketSeverity.low, "microstructure_spread"
    if "stale" in t or ctx.get("quote_age_sec", 0) > 30:
        return TicketType.data_quality_incident, TicketSeverity.medium, "data_stale"
    if "reconcil" in t:
        return TicketType.reconciliation_incident, TicketSeverity.high, "ops_reconciliation"
    if "win_rate" in t or "drawdown" in t:
        return TicketType.strategy_degradation, TicketSeverity.medium, "strategy_health"
    if "regime" in t:
        return TicketType.regime_shift, TicketSeverity.medium, "regime"
    if "scanner" in t or "scanner" in sc:
        return TicketType.scanner_gap, TicketSeverity.low, "scanner"
    if "false_positive" in t:
        return TicketType.false_positive_signal, TicketSeverity.low, "signals"
    if "false_negative" in t:
        return TicketType.false_negative_signal, TicketSeverity.medium, "signals"
    if "ratio" in t:
        return TicketType.ratio_problem, TicketSeverity.medium, "ratios"
    if "research" in t and "market" in t:
        return TicketType.market_research_needed, TicketSeverity.info, "research"
    if "research" in t and "instrument" in t:
        return TicketType.instrument_research_needed, TicketSeverity.info, "research"
    if "gate" in t and "design" in t:
        return TicketType.gate_design_review_needed, TicketSeverity.info, "design"
    if "avenue" in t and "design" in t:
        return TicketType.avenue_design_review_needed, TicketSeverity.info, "design"

    return TicketType.execution_incident, TicketSeverity.low, "general"
