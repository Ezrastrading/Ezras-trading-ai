"""Canonical artifact paths for multi-bot orchestration (under global_layer/_governance_data)."""

from __future__ import annotations

from pathlib import Path

from trading_ai.global_layer._bot_paths import global_layer_governance_dir


def orchestration_root() -> Path:
    p = global_layer_governance_dir() / "orchestration"
    p.mkdir(parents=True, exist_ok=True)
    return p


def execution_authority_path() -> Path:
    return orchestration_root() / "execution_authority.json"


def promotion_queue_path() -> Path:
    return orchestration_root() / "promotion_queue.json"


def demotion_queue_path() -> Path:
    return orchestration_root() / "demotion_queue.json"


def disable_queue_path() -> Path:
    return orchestration_root() / "disable_queue.json"


def conflict_log_path() -> Path:
    return orchestration_root() / "conflicts.jsonl"


def spawn_audit_path() -> Path:
    return orchestration_root() / "spawn_audit.jsonl"


def spawn_review_queue_path() -> Path:
    return orchestration_root() / "spawn_review_queue.json"


def duplicate_task_guard_path() -> Path:
    return orchestration_root() / "duplicate_task_guard.json"


def orchestration_health_path() -> Path:
    return orchestration_root() / "orchestration_health.json"


def ceo_daily_review_path() -> Path:
    return orchestration_root() / "ceo_daily_orchestration_review.json"


def operator_snapshot_path() -> Path:
    return orchestration_root() / "operator_snapshot.json"


def incidents_log_path() -> Path:
    return orchestration_root() / "incidents.jsonl"


def orchestration_kill_switch_path() -> Path:
    return orchestration_root() / "orchestration_kill_switch.json"


def promotion_contract_template_path() -> Path:
    return orchestration_root() / "promotion_contract.schema.json"


def last_spawn_ts_path() -> Path:
    return orchestration_root() / "last_spawn_ts.json"


def promotion_contract_policy_path() -> Path:
    return orchestration_root() / "promotion_contract_policy.json"


def capital_governor_policy_path() -> Path:
    return orchestration_root() / "capital_governor_policy.json"


def bot_auto_promotion_truth_path() -> Path:
    return orchestration_root() / "bot_auto_promotion_truth.json"


def bot_capital_authority_registry_path() -> Path:
    return orchestration_root() / "bot_capital_authority_registry.json"


def capital_scale_up_queue_path() -> Path:
    return orchestration_root() / "capital_scale_up_queue.json"


def capital_scale_down_queue_path() -> Path:
    return orchestration_root() / "capital_scale_down_queue.json"


def capital_freeze_events_path() -> Path:
    return orchestration_root() / "capital_freeze_events.jsonl"


def bot_permissions_matrix_path() -> Path:
    return orchestration_root() / "bot_permissions_matrix.json"


def bot_system_readiness_path() -> Path:
    return orchestration_root() / "bot_system_readiness.json"


def capital_governor_readiness_truth_path() -> Path:
    return orchestration_root() / "capital_governor_readiness_truth.json"


def bot_eval_signals_path(bot_id: str) -> Path:
    return orchestration_root() / "bot_eval_signals" / f"{bot_id}.json"


def orchestration_truth_chain_path() -> Path:
    return orchestration_root() / "orchestration_truth_chain.json"


def execution_intent_ledger_path() -> Path:
    return orchestration_root() / "execution_intent_ledger.jsonl"


def orchestration_risk_caps_path() -> Path:
    return orchestration_root() / "orchestration_risk_caps.json"


def orchestration_detection_snapshot_path() -> Path:
    return orchestration_root() / "orchestration_detection_snapshot.json"


def research_queue_path() -> Path:
    return orchestration_root() / "research_queue.json"


def experiment_queue_path() -> Path:
    return orchestration_root() / "experiment_queue.json"


def implementation_queue_path() -> Path:
    return orchestration_root() / "implementation_queue.json"


def validation_queue_path() -> Path:
    return orchestration_root() / "validation_queue.json"


def rapid_upside_queue_path() -> Path:
    return orchestration_root() / "rapid_upside_opportunities_queue.json"


def blocked_opportunities_path() -> Path:
    return orchestration_root() / "blocked_opportunity_reasons.json"


def edge_discovery_snapshot_path() -> Path:
    return orchestration_root() / "edge_discovery_snapshot.json"


def time_to_convergence_snapshot_path() -> Path:
    return orchestration_root() / "time_to_convergence_snapshot.json"


def autonomous_backbone_status_path() -> Path:
    return orchestration_root() / "autonomous_backbone_status.json"


def implementation_governor_state_path() -> Path:
    return orchestration_root() / "implementation_governor_state.json"
