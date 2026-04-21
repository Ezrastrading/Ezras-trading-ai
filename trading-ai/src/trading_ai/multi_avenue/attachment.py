"""Which universal layers auto-attach when an avenue or gate is registered."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def compute_auto_attach_layers(
    *,
    avenue_id: str,
    gate_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Framework-first attachment list for new wiring. Execution is never implied.

    Adding a new avenue id here (in avenue_registry) immediately yields these logical attachments.
    """
    layers: List[str] = [
        "avenue_registry_snapshot",
        "gate_registry_snapshot",
        "scoped_filesystem_directories",
        "multi_avenue_status_matrix",
        "contamination_guard_helpers",
        "progression_template_files",
        "scoped_ceo_session_shell",
        "scanner_framework_slot",
        "ratio_policy_pattern_via_universal_registry",
        "honest_live_status_matrix_row_hook",
        "intelligence_ticket_scope",
        "intelligence_learning_scope",
        "intelligence_ceo_review_pointers",
        "intelligence_daily_cycle_hooks",
        "intelligence_governance_bootstrap",
        "edge_research_subsystem",
        "edge_research_registry_and_rankings",
        "proving_artifact_catalog_pointers",
        "scoped_edge_research_snapshots_per_gate",
    ]
    if gate_id:
        layers.extend(
            [
                "gate_scoped_review_directory",
                "gate_scoped_progression_file",
                "scanner_placeholder_for_gate",
            ]
        )
    return {
        "avenue_id": avenue_id,
        "gate_id": gate_id,
        "auto_attach_layers": layers,
        "execution_not_auto_attached": True,
        "honest_note": "Execution modules must be wired explicitly per venue.",
    }


def synthetic_new_avenue_attachment_demo(new_avenue_id: str = "Z") -> Dict[str, Any]:
    """Test/demo hook for a hypothetical avenue — no I/O."""
    return compute_auto_attach_layers(avenue_id=new_avenue_id, gate_id="gate_new")
