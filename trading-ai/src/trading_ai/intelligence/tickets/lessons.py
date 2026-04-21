"""Extract lesson payloads for learning updater — no direct execution mutation."""

from __future__ import annotations

from typing import Any, Dict, List

from trading_ai.intelligence.tickets.models import Ticket


def ticket_to_lesson_payload(ticket: Ticket) -> Dict[str, Any]:
    """Structured lesson for ``intelligence.learning.updater`` — additive only."""
    return {
        "source_ticket_id": ticket.ticket_id,
        "avenue_id": ticket.avenue_id,
        "gate_id": ticket.gate_id,
        "venue": ticket.venue,
        "ticket_type": ticket.ticket_type.value,
        "human_summary": ticket.human_plain_english_summary,
        "machine_summary": ticket.machine_summary,
        "evidence_refs": ticket.evidence_refs,
        "confidence": ticket.confidence,
        "recommended_action": ticket.recommended_action,
    }


def lessons_from_tickets(tickets: List[Ticket]) -> List[Dict[str, Any]]:
    return [ticket_to_lesson_payload(t) for t in tickets if t.learning_update_required]
