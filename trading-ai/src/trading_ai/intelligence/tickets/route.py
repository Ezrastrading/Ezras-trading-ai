"""Explicit, traceable routing from ticket type/category to responsibility domains."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Set

from trading_ai.intelligence.paths import ticket_routing_log_jsonl_path
from trading_ai.intelligence.tickets.models import RouteResult, Ticket, TicketType

# Domains the user specified — single source for routing outputs.
DOMAIN_EXECUTION = "execution"
DOMAIN_POLICY = "policy"
DOMAIN_RATIOS = "ratios"
DOMAIN_RESERVE = "reserve"
DOMAIN_SCANNER = "scanner"
DOMAIN_ROUTING = "routing"
DOMAIN_VENUE_BEHAVIOR = "venue_behavior"
DOMAIN_MARKET_RESEARCH = "market_research"
DOMAIN_INSTRUMENT_RESEARCH = "instrument_research"
DOMAIN_EDGE_RESEARCH = "edge_research"
DOMAIN_CEO_REVIEW = "CEO_review"
DOMAIN_OPERATOR_ATTENTION = "operator_attention"
DOMAIN_AVENUE_DESIGN = "avenue_design"
DOMAIN_GATE_DESIGN = "gate_design"
DOMAIN_LEARNING_MEMORY = "learning_memory"

ALL_DOMAINS: Set[str] = {
    DOMAIN_EXECUTION,
    DOMAIN_POLICY,
    DOMAIN_RATIOS,
    DOMAIN_RESERVE,
    DOMAIN_SCANNER,
    DOMAIN_ROUTING,
    DOMAIN_VENUE_BEHAVIOR,
    DOMAIN_MARKET_RESEARCH,
    DOMAIN_INSTRUMENT_RESEARCH,
    DOMAIN_EDGE_RESEARCH,
    DOMAIN_CEO_REVIEW,
    DOMAIN_OPERATOR_ATTENTION,
    DOMAIN_AVENUE_DESIGN,
    DOMAIN_GATE_DESIGN,
    DOMAIN_LEARNING_MEMORY,
}


_TYPE_ROUTING: Dict[TicketType, List[str]] = {
    TicketType.execution_incident: [DOMAIN_EXECUTION, DOMAIN_ROUTING, DOMAIN_OPERATOR_ATTENTION],
    TicketType.market_structure_incident: [DOMAIN_MARKET_RESEARCH, DOMAIN_VENUE_BEHAVIOR, DOMAIN_ROUTING],
    TicketType.strategy_degradation: [DOMAIN_EDGE_RESEARCH, DOMAIN_SCANNER, DOMAIN_CEO_REVIEW],
    TicketType.edge_decay: [DOMAIN_EDGE_RESEARCH, DOMAIN_LEARNING_MEMORY],
    TicketType.edge_opportunity: [DOMAIN_EDGE_RESEARCH, DOMAIN_LEARNING_MEMORY],
    TicketType.liquidity_warning: [DOMAIN_VENUE_BEHAVIOR, DOMAIN_ROUTING],
    TicketType.slippage_warning: [DOMAIN_EXECUTION, DOMAIN_ROUTING],
    TicketType.partial_fill_incident: [DOMAIN_EXECUTION, DOMAIN_ROUTING],
    TicketType.timeout_incident: [DOMAIN_EXECUTION, DOMAIN_ROUTING],
    TicketType.quote_policy_mismatch: [DOMAIN_POLICY, DOMAIN_ROUTING],
    TicketType.runtime_policy_mismatch: [DOMAIN_POLICY, DOMAIN_OPERATOR_ATTENTION],
    TicketType.venue_behavior_change: [DOMAIN_VENUE_BEHAVIOR, DOMAIN_MARKET_RESEARCH],
    TicketType.data_quality_incident: [DOMAIN_ROUTING, DOMAIN_OPERATOR_ATTENTION],
    TicketType.reconciliation_incident: [DOMAIN_RATIOS, DOMAIN_RESERVE, DOMAIN_OPERATOR_ATTENTION],
    TicketType.ratio_problem: [DOMAIN_RATIOS, DOMAIN_POLICY],
    TicketType.reserve_problem: [DOMAIN_RESERVE, DOMAIN_POLICY],
    TicketType.operator_visibility_problem: [DOMAIN_OPERATOR_ATTENTION, DOMAIN_CEO_REVIEW],
    TicketType.false_positive_signal: [DOMAIN_SCANNER, DOMAIN_EDGE_RESEARCH],
    TicketType.false_negative_signal: [DOMAIN_SCANNER, DOMAIN_EDGE_RESEARCH],
    TicketType.scanner_gap: [DOMAIN_SCANNER, DOMAIN_GATE_DESIGN],
    TicketType.regime_shift: [DOMAIN_MARKET_RESEARCH, DOMAIN_EDGE_RESEARCH, DOMAIN_LEARNING_MEMORY],
    TicketType.pnl_anomaly: [DOMAIN_RATIOS, DOMAIN_CEO_REVIEW, DOMAIN_OPERATOR_ATTENTION],
    TicketType.learning_update_needed: [DOMAIN_LEARNING_MEMORY, DOMAIN_CEO_REVIEW],
    TicketType.market_research_needed: [DOMAIN_MARKET_RESEARCH, DOMAIN_LEARNING_MEMORY],
    TicketType.instrument_research_needed: [DOMAIN_INSTRUMENT_RESEARCH, DOMAIN_LEARNING_MEMORY],
    TicketType.gate_design_review_needed: [DOMAIN_GATE_DESIGN, DOMAIN_CEO_REVIEW],
    TicketType.avenue_design_review_needed: [DOMAIN_AVENUE_DESIGN, DOMAIN_CEO_REVIEW],
}


def route_ticket(
    ticket: Ticket,
    *,
    runtime_root: Optional[Path] = None,
    log: bool = True,
) -> RouteResult:
    """Compute domains for ``ticket`` and optionally append to routing log."""
    base = list(_TYPE_ROUTING.get(ticket.ticket_type, [DOMAIN_OPERATOR_ATTENTION]))
    if ticket.ceo_review_required and DOMAIN_CEO_REVIEW not in base:
        base.append(DOMAIN_CEO_REVIEW)
    if ticket.learning_update_required and DOMAIN_LEARNING_MEMORY not in base:
        base.append(DOMAIN_LEARNING_MEMORY)
    # Preserve order, dedupe
    seen: Set[str] = set()
    ordered: List[str] = []
    for d in base:
        if d not in seen:
            seen.add(d)
            ordered.append(d)

    rr = RouteResult(ticket_id=ticket.ticket_id, domains=ordered, routing_reason=f"type={ticket.ticket_type.value}")
    if log:
        log_path = ticket_routing_log_jsonl_path(runtime_root=runtime_root)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rr.model_dump(mode="json"), ensure_ascii=False) + "\n")
    return rr


def apply_routing_to_ticket(ticket: Ticket, rr: RouteResult) -> Ticket:
    """Mutate ticket with routed domains (caller persists via store)."""
    ticket.routed_domains = rr.domains
    ticket.status = ticket.status  # no-op; keep pydantic happy
    return ticket
