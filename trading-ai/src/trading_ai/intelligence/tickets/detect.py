"""Automatic detection → structured ticket drafts (no live trading side effects)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from trading_ai.intelligence.tickets.classify import classify_signal
from trading_ai.intelligence.tickets.models import (
    Ticket,
    TicketSeverity,
    TicketStatus,
    TicketType,
)


def _base_context(
    *,
    avenue_id: str = "",
    gate_id: str = "",
    venue: str = "",
    market_type: str = "",
    instrument_type: str = "",
    product_id: str = "",
    contract_id: str = "",
    market_id: str = "",
    source_component: str = "detect",
) -> Dict[str, Any]:
    return {
        "avenue_id": avenue_id,
        "gate_id": gate_id,
        "venue": venue,
        "market_type": market_type,
        "instrument_type": instrument_type,
        "product_id": product_id,
        "contract_id": contract_id,
        "market_id": market_id,
        "source_component": source_component,
    }


def detect_from_execution_event(event: Dict[str, Any]) -> List[Ticket]:
    """A. Execution issues — from a normalized execution/diagnostic event dict."""
    out: List[Ticket] = []
    trigger = str(event.get("trigger") or event.get("reason") or event.get("message") or "")
    ctx = {
        **_base_context(
            avenue_id=str(event.get("avenue_id", "")),
            gate_id=str(event.get("gate_id", "")),
            venue=str(event.get("venue", "")),
            market_type=str(event.get("market_type", "")),
            instrument_type=str(event.get("instrument_type", "")),
            product_id=str(event.get("product_id", "")),
            contract_id=str(event.get("contract_id", "")),
            market_id=str(event.get("market_id", "")),
            source_component=str(event.get("source_component", "execution")),
        ),
        **event,
    }
    tt, sev, cat = classify_signal(trigger=trigger, source_component=str(event.get("source_component", "")), context=ctx)

    summ = event.get("human_summary") or f"Execution signal: {trigger or 'unspecified'}"
    t = Ticket(
        ticket_type=tt,
        severity=sev,
        category=cat,
        avenue_id=str(event.get("avenue_id", "")),
        gate_id=str(event.get("gate_id", "")),
        venue=str(event.get("venue", "")),
        market_type=str(event.get("market_type", "")),
        instrument_type=str(event.get("instrument_type", "")),
        product_id=str(event.get("product_id", "")),
        contract_id=str(event.get("contract_id", "")),
        market_id=str(event.get("market_id", "")),
        source_component=str(event.get("source_component", "execution")),
        trigger_event=trigger,
        human_plain_english_summary=summ,
        machine_summary=str(event.get("machine_summary") or event),
        evidence_refs=list(event.get("evidence_refs") or []),
        likely_root_cause=str(event.get("likely_root_cause") or ""),
        confidence=float(event.get("confidence", 0.5)),
        recommended_action=str(event.get("recommended_action") or "Review execution logs and venue response."),
        auto_action_taken=str(event.get("auto_action_taken") or ""),
        ceo_review_required=sev in (TicketSeverity.medium, TicketSeverity.high, TicketSeverity.critical),
        learning_update_required=tt in (TicketType.venue_behavior_change, TicketType.runtime_policy_mismatch),
        safe_to_auto_close=sev == TicketSeverity.info and tt == TicketType.liquidity_warning,
    )
    out.append(t)
    return out


def detect_from_strategy_health(metrics: Dict[str, Any]) -> List[Ticket]:
    """B. Strategy health — win rate, drawdown, momentum, utilization."""
    out: List[Ticket] = []
    wr = metrics.get("win_rate")
    dd = metrics.get("max_drawdown_pct")
    mom = metrics.get("gate_b_momentum_score")
    util = metrics.get("capital_utilization_pct")

    base = _base_context(
        avenue_id=str(metrics.get("avenue_id", "")),
        gate_id=str(metrics.get("gate_id", "")),
        venue=str(metrics.get("venue", "")),
        market_type=str(metrics.get("market_type", "")),
        instrument_type=str(metrics.get("instrument_type", "")),
        source_component="strategy_health",
    )

    if wr is not None and float(wr) < float(metrics.get("win_rate_floor", 0.35)):
        out.append(
            Ticket(
                ticket_type=TicketType.strategy_degradation,
                severity=TicketSeverity.medium,
                category="win_rate",
                **base,
                trigger_event="win_rate_below_floor",
                human_plain_english_summary=f"Win rate {wr} below configured floor.",
                machine_summary=str(metrics),
                evidence_refs=list(metrics.get("evidence_refs") or []),
                confidence=0.55,
                ceo_review_required=True,
                learning_update_required=True,
            )
        )
    if dd is not None and float(dd) > float(metrics.get("drawdown_alert_pct", 0.15)):
        out.append(
            Ticket(
                ticket_type=TicketType.pnl_anomaly,
                severity=TicketSeverity.high,
                category="drawdown",
                **base,
                trigger_event="abnormal_drawdown",
                human_plain_english_summary=f"Drawdown {dd} exceeds alert threshold.",
                machine_summary=str(metrics),
                evidence_refs=list(metrics.get("evidence_refs") or []),
                confidence=0.6,
                ceo_review_required=True,
            )
        )
    if mom is not None and float(mom) < float(metrics.get("momentum_floor", 0.2)):
        out.append(
            Ticket(
                ticket_type=TicketType.strategy_degradation,
                severity=TicketSeverity.medium,
                category="gate_b_momentum",
                **base,
                trigger_event="gate_b_momentum_deterioration",
                human_plain_english_summary="Gate B momentum deteriorated versus baseline.",
                machine_summary=str(metrics),
                evidence_refs=list(metrics.get("evidence_refs") or []),
                confidence=0.5,
                ceo_review_required=True,
            )
        )
    if util is not None and float(util) < float(metrics.get("utilization_low_pct", 0.05)):
        out.append(
            Ticket(
                ticket_type=TicketType.ratio_problem,
                severity=TicketSeverity.low,
                category="capital_utilization",
                **base,
                trigger_event="low_capital_utilization",
                human_plain_english_summary="Capital utilization unusually low — check sizing and gates.",
                machine_summary=str(metrics),
                confidence=0.45,
            )
        )
    return out


def detect_from_market_microstructure(snapshot: Dict[str, Any]) -> List[Ticket]:
    """C. Market / venue — spreads, staleness, volatility flags."""
    out: List[Ticket] = []
    spread_pct = float(snapshot.get("spread_pct") or 0)
    age = float(snapshot.get("quote_age_sec") or 0)
    vol = str(snapshot.get("volatility_regime") or "")
    base = _base_context(
        avenue_id=str(snapshot.get("avenue_id", "")),
        gate_id=str(snapshot.get("gate_id", "")),
        venue=str(snapshot.get("venue", "")),
        market_type=str(snapshot.get("market_type", "")),
        instrument_type=str(snapshot.get("instrument_type", "")),
        market_id=str(snapshot.get("market_id", "")),
        source_component="market_microstructure",
    )
    if spread_pct > float(snapshot.get("spread_warn_pct", 0.01)):
        out.append(
            Ticket(
                ticket_type=TicketType.liquidity_warning,
                severity=TicketSeverity.low,
                category="spread",
                **base,
                trigger_event="widened_spread",
                human_plain_english_summary=f"Spread {spread_pct:.4f} wider than warning threshold.",
                machine_summary=str(snapshot),
                confidence=0.55,
            )
        )
    if age > float(snapshot.get("stale_quote_sec", 30)):
        out.append(
            Ticket(
                ticket_type=TicketType.data_quality_incident,
                severity=TicketSeverity.medium,
                category="stale_quote",
                **base,
                trigger_event="stale_quotes",
                human_plain_english_summary=f"Quotes stale ({age}s) — data or venue path may be impaired.",
                machine_summary=str(snapshot),
                ceo_review_required=True,
                confidence=0.5,
            )
        )
    if vol in ("extreme_chop", "liquidity_collapse"):
        out.append(
            Ticket(
                ticket_type=TicketType.market_structure_incident,
                severity=TicketSeverity.medium,
                category="conditions",
                **base,
                trigger_event=vol,
                human_plain_english_summary="Unusual market conditions flagged by microstructure monitor.",
                machine_summary=str(snapshot),
                ceo_review_required=True,
                learning_update_required=True,
                confidence=0.45,
            )
        )
    return out


def detect_from_system_intelligence(signals: Dict[str, Any]) -> List[Ticket]:
    """D. Intelligence layer issues — ambiguity, missing artifacts, blind spots."""
    out: List[Ticket] = []
    base = _base_context(
        avenue_id=str(signals.get("avenue_id", "")),
        source_component="intelligence_layer",
    )
    if int(signals.get("ceo_ambiguity_count", 0)) >= int(signals.get("ambiguity_threshold", 3)):
        out.append(
            Ticket(
                ticket_type=TicketType.operator_visibility_problem,
                severity=TicketSeverity.medium,
                category="ceo_ambiguity",
                **base,
                trigger_event="repeated_ceo_ambiguity",
                human_plain_english_summary="Repeated ambiguity in CEO sessions — tighten evidence and definitions.",
                machine_summary=str(signals),
                ceo_review_required=True,
                confidence=0.55,
            )
        )
    if signals.get("missing_artifacts"):
        out.append(
            Ticket(
                ticket_type=TicketType.data_quality_incident,
                severity=TicketSeverity.medium,
                category="missing_artifacts",
                **base,
                trigger_event="missing_artifacts",
                human_plain_english_summary="Expected review artifacts missing from scoped paths.",
                machine_summary=str(signals.get("missing_artifacts")),
                ceo_review_required=True,
                confidence=0.5,
            )
        )
    if int(signals.get("unresolved_same_category_count", 0)) >= int(signals.get("unresolved_threshold", 5)):
        out.append(
            Ticket(
                ticket_type=TicketType.learning_update_needed,
                severity=TicketSeverity.medium,
                category="ticket_backlog",
                **base,
                trigger_event="repeated_unresolved_category",
                human_plain_english_summary="Many unresolved tickets in the same category — needs routing and learning.",
                machine_summary=str(signals),
                learning_update_required=True,
                ceo_review_required=True,
                confidence=0.5,
            )
        )
    return out


def detect_opportunities(opportunity: Dict[str, Any]) -> List[Ticket]:
    """E. Opportunity detection — edges, routes, regime where strategy excels."""
    out: List[Ticket] = []
    base = _base_context(
        avenue_id=str(opportunity.get("avenue_id", "")),
        gate_id=str(opportunity.get("gate_id", "")),
        venue=str(opportunity.get("venue", "")),
        market_type=str(opportunity.get("market_type", "")),
        instrument_type=str(opportunity.get("instrument_type", "")),
        source_component="opportunity_detector",
    )
    if opportunity.get("repeatable_edge_emerging"):
        out.append(
            Ticket(
                ticket_type=TicketType.edge_opportunity,
                severity=TicketSeverity.info,
                category="edge",
                **base,
                trigger_event="repeatable_edge",
                human_plain_english_summary=str(
                    opportunity.get("summary") or "Repeatable edge emerging — validate before scaling."
                ),
                machine_summary=str(opportunity),
                evidence_refs=list(opportunity.get("evidence_refs") or []),
                learning_update_required=True,
                confidence=float(opportunity.get("confidence", 0.4)),
            )
        )
    if opportunity.get("better_route_detected"):
        out.append(
            Ticket(
                ticket_type=TicketType.edge_opportunity,
                severity=TicketSeverity.info,
                category="routing",
                **base,
                trigger_event="better_route",
                human_plain_english_summary="Potential better execution route detected — simulation required.",
                machine_summary=str(opportunity),
                confidence=float(opportunity.get("confidence", 0.35)),
            )
        )
    if opportunity.get("exceptional_regime_fit"):
        out.append(
            Ticket(
                ticket_type=TicketType.edge_opportunity,
                severity=TicketSeverity.info,
                category="regime_fit",
                **base,
                trigger_event="strategy_regime_fit",
                human_plain_english_summary="Strategy performing strongly under a specific regime — document and monitor.",
                machine_summary=str(opportunity),
                learning_update_required=True,
                confidence=float(opportunity.get("confidence", 0.4)),
            )
        )
    return out


def maybe_create_research_ticket(
    *,
    unknown_topic: str,
    why_it_matters: str,
    domain_file_to_update: str,
    avenue_id: str = "",
    venue: str = "",
) -> Ticket:
    """Part 9 — explicit research ticket with investigation targets."""
    return Ticket(
        ticket_type=TicketType.market_research_needed,
        severity=TicketSeverity.info,
        category="auto_research",
        avenue_id=avenue_id,
        venue=venue,
        source_component="research_queue",
        trigger_event="knowledge_gap",
        human_plain_english_summary=f"Unknown: {unknown_topic}. Why it matters: {why_it_matters}",
        machine_summary=f"domain_file_to_update={domain_file_to_update}",
        evidence_refs=[],
        recommended_action=f"Investigate and update domain file `{domain_file_to_update}` with evidence.",
        learning_update_required=True,
        ceo_review_required=False,
        confidence=0.3,
        extra={
            "what_is_unknown": unknown_topic,
            "why_it_matters": why_it_matters,
            "domain_file_to_update": domain_file_to_update,
        },
    )


def run_detection_suite(
    *,
    execution_events: Optional[List[Dict[str, Any]]] = None,
    strategy_metrics: Optional[Dict[str, Any]] = None,
    market_snapshot: Optional[Dict[str, Any]] = None,
    system_signals: Optional[Dict[str, Any]] = None,
    opportunities: Optional[List[Dict[str, Any]]] = None,
) -> List[Ticket]:
    """Run configured detectors and return ticket drafts (caller persists)."""
    tickets: List[Ticket] = []
    for ev in execution_events or []:
        tickets.extend(detect_from_execution_event(ev))
    if strategy_metrics:
        tickets.extend(detect_from_strategy_health(strategy_metrics))
    if market_snapshot:
        tickets.extend(detect_from_market_microstructure(market_snapshot))
    if system_signals:
        tickets.extend(detect_from_system_intelligence(system_signals))
    for op in opportunities or []:
        tickets.extend(detect_opportunities(op))
    return tickets
