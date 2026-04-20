"""
Truth scopes — explicit source priority and honesty rules (advisory; does not change governance).

Each consumer must declare which scope it uses; outputs must include ``source_policy_used``.
"""

from __future__ import annotations

from typing import Any, Dict

TRUTH_VERSION = "ezras_truth_contract_v1"

# --- Scope identifiers (string constants for JSON stability) ---

REVIEW_TRUTH = "review_truth"  # AI packets, joint review enrichment — federated trades preferred
RUNTIME_TRUTH = "runtime_truth"  # Live organism / NTE operational memory
CAPITAL_TRUTH = "capital_truth"  # Ledger + realized totals — ledger wins on conflict
GOAL_TRUTH = "goal_truth"  # Goal evaluation — metric-specific sources (see below)
OPERATOR_TRUTH = "operator_truth"  # Command center / status dashboards — merged explicit
CEO_TRUTH = "ceo_truth"  # CEO orchestration + structured session artifacts


def policy_for_review() -> Dict[str, Any]:
    return {
        "scope": REVIEW_TRUTH,
        "truth_version": TRUTH_VERSION,
        "primary_source": "federated_trades",
        "precedence": ["nte_trade_memory", "trade_events_jsonl"],
        "fallback_order": ["nte_trade_memory_only"],
        "discrepancy_behavior": "expose_in_packet_truth_meta_and_discrepancy_report",
        "honesty": "Federation merges memory + databank with explicit conflict records; never silent overwrite.",
    }


def policy_for_runtime() -> Dict[str, Any]:
    return {
        "scope": RUNTIME_TRUTH,
        "truth_version": TRUTH_VERSION,
        "primary_source": "nte_trade_memory_json",
        "precedence": ["nte_trade_memory"],
        "fallback_order": [],
        "discrepancy_behavior": "runtime_uses_memory_only; cross_check_vs_federated_optional",
        "honesty": "Operational closes are logged to NTE memory first; databank is enrichment for review.",
    }


def policy_for_capital() -> Dict[str, Any]:
    return {
        "scope": CAPITAL_TRUTH,
        "truth_version": TRUTH_VERSION,
        "primary_source": "nte_capital_ledger_json",
        "precedence": ["capital_ledger"],
        "fallback_order": [],
        "discrepancy_behavior": "compare_sum_closed_trades_vs_ledger_realized_when_requested",
        "honesty": "Realized totals for capital posture come from ledger; trade sums are a cross-check.",
    }


def policy_for_goal_metrics() -> Dict[str, Any]:
    return {
        "scope": GOAL_TRUTH,
        "truth_version": TRUTH_VERSION,
        "by_goal": {
            "GOAL_A": {
                "primary": "ledger.realized_pnl_net",
                "secondary_check": "sum_normalized_closed_trade_net_pnl",
            },
            "GOAL_B": {
                "primary": "iso_week_buckets_from_normalized_trades",
                "secondary_check": "ledger.rolling_7d_net_profit",
            },
            "GOAL_C": {
                "primary": "per_avenue_weekly_net_from_normalized_trades",
                "secondary_check": None,
            },
            "GOAL_D": {
                "primary": "per_avenue_weekly_net_from_normalized_trades",
                "secondary_check": None,
            },
        },
        "discrepancy_behavior": "emit_goal_truth_discrepancies_when_ledger_and_trades_diverge",
        "honesty": "Goals never infer profit from ambiguous rows; missing ts/avenue excluded with counts.",
    }


def policy_for_operator() -> Dict[str, Any]:
    return {
        "scope": OPERATOR_TRUTH,
        "truth_version": TRUTH_VERSION,
        "primary_source": "merged_explicit_sections",
        "sections": [
            "command_center_snapshot",
            "execution_intelligence_snapshot",
            "autonomous_operator_path",
            "gate_readiness_artifacts",
        ],
        "discrepancy_behavior": "truth_source_summary_lists_each_section_source",
        "honesty": "Operator view may show both federated and NTE slices side-by-side when both exist.",
    }


def policy_for_ceo() -> Dict[str, Any]:
    return {
        "scope": CEO_TRUTH,
        "truth_version": TRUTH_VERSION,
        "primary_source": "ceo_session_history_json_then_markdown_mirror",
        "precedence": ["ceo_session_history.jsonl", "ceo_session_structured_latest.json", "ceo_sessions.md"],
        "discrepancy_behavior": "prefer_structured_json_when_present",
        "honesty": "Structured sessions are machine source of truth; markdown is human mirror.",
    }


def summarize_policies() -> Dict[str, Dict[str, Any]]:
    return {
        "review_truth": policy_for_review(),
        "runtime_truth": policy_for_runtime(),
        "capital_truth": policy_for_capital(),
        "goal_truth": policy_for_goal_metrics(),
        "operator_truth": policy_for_operator(),
        "ceo_truth": policy_for_ceo(),
    }


def policy_for_goal() -> Dict[str, Any]:
    """Alias for goal-scoped evaluation (same as ``summarize_policies()['goal_truth']``)."""
    return policy_for_goal_metrics()
