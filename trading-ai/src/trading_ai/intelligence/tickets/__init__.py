"""Internal incident/ticket system — detection, routing, CEO review, append-only store."""

from trading_ai.intelligence.tickets.ceo_review import (
    append_daily_ceo_rollup,
    build_ceo_session,
    should_emit_ceo_session,
    write_ceo_session_files,
    write_daily_learning_session_review,
)
from trading_ai.intelligence.tickets.classify import classify_signal
from trading_ai.intelligence.tickets.close import archive_ticket, close_ticket, update_ticket_status
from trading_ai.intelligence.tickets.detect import (
    detect_from_execution_event,
    detect_from_market_microstructure,
    detect_from_strategy_health,
    detect_from_system_intelligence,
    detect_opportunities,
    maybe_create_research_ticket,
    run_detection_suite,
)
from trading_ai.intelligence.tickets.lessons import lessons_from_tickets, ticket_to_lesson_payload
from trading_ai.intelligence.tickets.models import (
    CEOSessionArtifact,
    RouteResult,
    Ticket,
    TicketSeverity,
    TicketStatus,
    TicketType,
    new_ticket_id,
)
from trading_ai.intelligence.tickets.route import apply_routing_to_ticket, route_ticket
from trading_ai.intelligence.tickets.store import append_ticket, load_all_tickets, load_ticket_by_id, rebuild_indexes

__all__ = [
    "Ticket",
    "TicketType",
    "TicketStatus",
    "TicketSeverity",
    "CEOSessionArtifact",
    "RouteResult",
    "new_ticket_id",
    "classify_signal",
    "route_ticket",
    "apply_routing_to_ticket",
    "append_ticket",
    "load_all_tickets",
    "load_ticket_by_id",
    "rebuild_indexes",
    "detect_from_execution_event",
    "detect_from_strategy_health",
    "detect_from_market_microstructure",
    "detect_from_system_intelligence",
    "detect_opportunities",
    "maybe_create_research_ticket",
    "run_detection_suite",
    "build_ceo_session",
    "write_ceo_session_files",
    "should_emit_ceo_session",
    "append_daily_ceo_rollup",
    "write_daily_learning_session_review",
    "ticket_to_lesson_payload",
    "lessons_from_tickets",
    "update_ticket_status",
    "close_ticket",
    "archive_ticket",
]
