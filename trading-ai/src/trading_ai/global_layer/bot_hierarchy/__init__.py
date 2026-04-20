"""Avenue Master / Gate Manager / Worker hierarchy — intelligence layer (no live authority)."""

from trading_ai.global_layer.bot_hierarchy.gate_discovery import (
    advance_gate_candidate_stage,
    build_gate_candidate_from_review_stub,
    discover_gate_candidate,
)
from trading_ai.global_layer.bot_hierarchy.integration import (
    build_ceo_hierarchy_attachment,
    build_execution_intelligence_hierarchy_advisory,
    build_review_packet_hierarchy_section,
    hierarchy_health_report,
)
from trading_ai.global_layer.bot_hierarchy.models import (
    GateCandidateRecord,
    GateCandidateStage,
    HierarchyBotRecord,
    HierarchyBotType,
)
from trading_ai.global_layer.bot_hierarchy.registry import (
    EZRA_GOVERNOR_BOT_ID,
    ensure_avenue_master,
    ensure_ezra_governor,
    list_bots,
    load_hierarchy_state,
    save_hierarchy_state,
)

__all__ = [
    "EZRA_GOVERNOR_BOT_ID",
    "GateCandidateRecord",
    "GateCandidateStage",
    "HierarchyBotRecord",
    "HierarchyBotType",
    "advance_gate_candidate_stage",
    "build_ceo_hierarchy_attachment",
    "build_execution_intelligence_hierarchy_advisory",
    "build_gate_candidate_from_review_stub",
    "build_review_packet_hierarchy_section",
    "discover_gate_candidate",
    "ensure_avenue_master",
    "ensure_ezra_governor",
    "hierarchy_health_report",
    "list_bots",
    "load_hierarchy_state",
    "save_hierarchy_state",
]
