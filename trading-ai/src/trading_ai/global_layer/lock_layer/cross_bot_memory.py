"""Cross-bot knowledge sharing rules — local vs lane vs avenue vs global (curated)."""

from __future__ import annotations

from typing import Any, Dict, List

MEMORY_SCOPE_RULES: Dict[str, Any] = {
    "truth_version": "memory_scope_rules_v1",
    "local_only": [
        "raw_trade_telemetry",
        "ephemeral_scratch",
        "credential_handles",
        "per-bot working_notes",
    ],
    "lane_wide_shareable": [
        "distilled_lessons_after_CEO_approval",
        "gate_specific_calibration_hints",
        "replay_summaries_for_lane",
    ],
    "avenue_wide_shareable": [
        "venue_risk_posture",
        "approved_strategy_changelog_refs",
        "non-portable_but_avenue_standard_playbooks",
    ],
    "global_curated": [
        "constitution_and_policy_refs",
        "benchmark_baselines",
        "incident_pattern_taxonomy",
    ],
    "forbidden_blind_spill": [
        "Coinbase-specific fills into Kalshi execution heuristics without translation_layer",
        "unapproved_shared_lessons",
        "credentials_or_secrets",
    ],
}
