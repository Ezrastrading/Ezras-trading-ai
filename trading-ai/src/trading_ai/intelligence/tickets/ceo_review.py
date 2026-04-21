"""Structured CEO session artifacts per ticket + daily rollup."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.intelligence.paths import (
    daily_learning_session_json_path,
    daily_learning_session_txt_path,
    ticket_ceo_daily_rollup_json_path,
    ticket_ceo_daily_rollup_txt_path,
    ticket_ceo_sessions_dir,
)
from trading_ai.intelligence.tickets.models import CEOSessionArtifact, Ticket, TicketSeverity, TicketType


def _infer_error_class(ticket: Ticket) -> str:
    t = ticket.ticket_type
    if t in (
        TicketType.execution_incident,
        TicketType.partial_fill_incident,
        TicketType.timeout_incident,
        TicketType.slippage_warning,
    ):
        return "execution"
    if t in (
        TicketType.market_structure_incident,
        TicketType.regime_shift,
        TicketType.liquidity_warning,
    ):
        return "market"
    if t in (
        TicketType.quote_policy_mismatch,
        TicketType.runtime_policy_mismatch,
    ):
        return "policy"
    if t in (
        TicketType.strategy_degradation,
        TicketType.edge_decay,
        TicketType.false_positive_signal,
        TicketType.false_negative_signal,
    ):
        return "strategy"
    if t == TicketType.operator_visibility_problem:
        return "operator"
    return "unknown"


def build_ceo_session(ticket: Ticket, *, operator_notes: str = "") -> CEOSessionArtifact:
    """Deterministic structured session from ticket fields (no fabricated facts)."""
    err = _infer_error_class(ticket)
    assumption = "Unknown — see evidence_refs and machine_summary."
    if ticket.likely_root_cause:
        assumption = f"Likely broken assumption: {ticket.likely_root_cause}"
    has_evidence = bool(ticket.evidence_refs)
    stmt_mode: str = "templated_summary"
    conf_basis = "ticket_fields_only"
    if has_evidence:
        stmt_mode = "evidence_backed_summary"
        conf_basis = "ticket_fields_plus_evidence_refs"
    if ticket.ceo_review_required or ticket.severity in (TicketSeverity.high, TicketSeverity.critical):
        stmt_mode = "operator_follow_up_required"
    return CEOSessionArtifact(
        ticket_id=ticket.ticket_id,
        what_happened=ticket.human_plain_english_summary or ticket.trigger_event,
        why_it_likely_happened=ticket.machine_summary[:2000] if ticket.machine_summary else ticket.likely_root_cause,
        what_system_assumption_broke=assumption,
        what_should_not_be_done_next_time="Do not scale or automate without confirming root cause and policy alignment.",
        what_should_be_done_next_time=ticket.recommended_action or "Gather evidence, route owners, update learning with proven facts only.",
        error_class=err,  # type: ignore[arg-type]
        was_avoidable=None,
        policy_should_change=ticket.ticket_type
        in (TicketType.quote_policy_mismatch, TicketType.runtime_policy_mismatch, TicketType.ratio_problem),
        strategy_should_change=ticket.ticket_type
        in (TicketType.strategy_degradation, TicketType.edge_decay, TicketType.regime_shift),
        market_conditions_changed=ticket.ticket_type
        in (TicketType.market_structure_incident, TicketType.regime_shift, TicketType.liquidity_warning),
        one_off_or_pattern="unclear",
        alter_gate_a_gate_b_or_avenue_behavior="Review scoped gates and avenue policies before behavior change; no blind copy.",
        operator_intervention_needed=ticket.severity in (TicketSeverity.high, TicketSeverity.critical)
        or ticket.ceo_review_required,
        ai_can_safely_update_learning_files=ticket.learning_update_required
        and ticket.confidence >= 0.6
        and bool(ticket.evidence_refs),
        confidence=ticket.confidence,
        ceo_statement_mode=stmt_mode,  # type: ignore[arg-type]
        evidence_chain_present=has_evidence,
        venue_truth_verified=False,
        confidence_basis=conf_basis,
        independent_verification_performed=False,
    )


def write_ceo_session_files(
    ticket: Ticket,
    *,
    runtime_root: Optional[Path] = None,
    operator_notes: str = "",
) -> CEOSessionArtifact:
    """Write ``<ticket_id>.json`` and ``.txt`` under ticket_ceo_sessions."""
    art = build_ceo_session(ticket, operator_notes=operator_notes)
    d = ticket_ceo_sessions_dir(runtime_root=runtime_root)
    jid = ticket.ticket_id.replace("/", "_")
    jp = d / f"{jid}.json"
    tp = d / f"{jid}.txt"
    jp.write_text(json.dumps(art.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    txt_lines = [
        f"Ticket: {art.ticket_id}",
        f"Generated: {art.generated_at}",
        "",
        "What happened:",
        art.what_happened,
        "",
        "Why it likely happened:",
        art.why_it_likely_happened,
        "",
        "Broken assumption:",
        art.what_system_assumption_broke,
        "",
        "What NOT to do next time:",
        art.what_should_not_be_done_next_time,
        "",
        "What TO do next time:",
        art.what_should_be_done_next_time,
        "",
        f"Error class (execution/market/policy/strategy/operator): {art.error_class}",
        f"Operator notes: {operator_notes or '(none)'}",
    ]
    tp.write_text("\n".join(txt_lines) + "\n", encoding="utf-8")
    return art


def should_emit_ceo_session(ticket: Ticket, *, repeated_low_ids: Optional[List[str]] = None) -> bool:
    """Medium/high/critical, or repeated low-severity pattern."""
    if ticket.severity in (TicketSeverity.medium, TicketSeverity.high, TicketSeverity.critical):
        return True
    if ticket.severity == TicketSeverity.low and repeated_low_ids and ticket.ticket_id in repeated_low_ids:
        return True
    return False


def append_daily_ceo_rollup(
    sessions: List[CEOSessionArtifact],
    *,
    runtime_root: Optional[Path] = None,
    day: Optional[date] = None,
) -> Dict[str, Any]:
    """Merge into daily rollup JSON/TXT."""
    day = day or datetime.now(timezone.utc).date()
    payload: Dict[str, Any] = {
        "date": day.isoformat(),
        "utc_generated_at": datetime.now(timezone.utc).isoformat(),
        "session_count": len(sessions),
        "tickets": [s.model_dump(mode="json") for s in sessions],
    }
    jp = ticket_ceo_daily_rollup_json_path(runtime_root=runtime_root)
    tp = ticket_ceo_daily_rollup_txt_path(runtime_root=runtime_root)
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = [
        f"CEO ticket session rollup — {day.isoformat()}",
        f"sessions: {len(sessions)}",
        "",
    ]
    for s in sessions:
        lines.append(f"- {s.ticket_id}: {s.what_happened[:120]}")
    tp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload


def write_daily_learning_session_review(
    body: Dict[str, Any],
    *,
    runtime_root: Optional[Path] = None,
) -> None:
    """Used by daily_cycle for review mirror files."""
    jp = daily_learning_session_json_path(runtime_root=runtime_root)
    tp = daily_learning_session_txt_path(runtime_root=runtime_root)
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tp.write_text(
        "\n".join(
            [
                f"Daily learning session — {body.get('date', '')}",
                str(body.get("summary_text", "")),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
