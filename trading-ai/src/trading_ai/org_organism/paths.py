"""Artifact paths for the mission / organism coordination layer (under ``data/control/organism``)."""

from __future__ import annotations

from pathlib import Path

from trading_ai.runtime_paths import ezras_runtime_root


def organism_data_dir(runtime_root: Path | None = None) -> Path:
    root = Path(runtime_root).resolve() if runtime_root is not None else ezras_runtime_root()
    p = root / "data" / "control" / "organism"
    p.mkdir(parents=True, exist_ok=True)
    return p


def mission_execution_state_path(runtime_root: Path | None = None) -> Path:
    return organism_data_dir(runtime_root) / "mission_execution_state.json"


def avenue_goal_state_path(runtime_root: Path | None = None) -> Path:
    return organism_data_dir(runtime_root) / "avenue_goal_state.json"


def gate_goal_state_path(runtime_root: Path | None = None) -> Path:
    return organism_data_dir(runtime_root) / "gate_goal_state.json"


def mission_progress_timeline_path(runtime_root: Path | None = None) -> Path:
    return organism_data_dir(runtime_root) / "mission_progress_timeline.jsonl"


def today_best_actions_path(runtime_root: Path | None = None) -> Path:
    return organism_data_dir(runtime_root) / "today_best_actions.json"


def tomorrow_best_actions_path(runtime_root: Path | None = None) -> Path:
    return organism_data_dir(runtime_root) / "tomorrow_best_actions.json"


def opportunity_pressure_snapshot_path(runtime_root: Path | None = None) -> Path:
    return organism_data_dir(runtime_root) / "opportunity_pressure_snapshot.json"


def avenue_priority_queue_path(runtime_root: Path | None = None) -> Path:
    return organism_data_dir(runtime_root) / "avenue_priority_queue.json"


def gate_priority_queue_path(runtime_root: Path | None = None) -> Path:
    return organism_data_dir(runtime_root) / "gate_priority_queue.json"


def experiment_priority_queue_path(runtime_root: Path | None = None) -> Path:
    return organism_data_dir(runtime_root) / "experiment_priority_queue.json"


def blocker_priority_queue_path(runtime_root: Path | None = None) -> Path:
    return organism_data_dir(runtime_root) / "blocker_priority_queue.json"


def experiment_registry_path(runtime_root: Path | None = None) -> Path:
    return organism_data_dir(runtime_root) / "experiment_registry.json"


def experiment_results_path(runtime_root: Path | None = None) -> Path:
    return organism_data_dir(runtime_root) / "experiment_results.jsonl"


def experiment_summary_by_gate_path(runtime_root: Path | None = None) -> Path:
    return organism_data_dir(runtime_root) / "experiment_summary_by_gate.json"


def experiment_summary_by_avenue_path(runtime_root: Path | None = None) -> Path:
    return organism_data_dir(runtime_root) / "experiment_summary_by_avenue.json"


def bot_scorecard_path(runtime_root: Path | None = None) -> Path:
    return organism_data_dir(runtime_root) / "bot_scorecard.json"


def avenue_master_scorecard_path(runtime_root: Path | None = None) -> Path:
    return organism_data_dir(runtime_root) / "avenue_master_scorecard.json"


def gate_manager_scorecard_path(runtime_root: Path | None = None) -> Path:
    return organism_data_dir(runtime_root) / "gate_manager_scorecard.json"


def worker_bot_scorecard_path(runtime_root: Path | None = None) -> Path:
    return organism_data_dir(runtime_root) / "worker_bot_scorecard.json"


def organism_advisory_queue_path(runtime_root: Path | None = None) -> Path:
    return organism_data_dir(runtime_root) / "organism_advisory_queue.jsonl"


def waste_detector_snapshot_path(runtime_root: Path | None = None) -> Path:
    return organism_data_dir(runtime_root) / "waste_detector_snapshot.json"


def repeated_failure_signatures_path(runtime_root: Path | None = None) -> Path:
    return organism_data_dir(runtime_root) / "repeated_failure_signatures.json"


def drag_sources_path(runtime_root: Path | None = None) -> Path:
    return organism_data_dir(runtime_root) / "drag_sources.json"


def promotion_bottlenecks_path(runtime_root: Path | None = None) -> Path:
    return organism_data_dir(runtime_root) / "promotion_bottlenecks.json"


def idle_capital_causes_path(runtime_root: Path | None = None) -> Path:
    return organism_data_dir(runtime_root) / "idle_capital_causes.json"


def supervised_readiness_closer_path(runtime_root: Path | None = None) -> Path:
    return organism_data_dir(runtime_root) / "supervised_readiness_closer.json"


def supervised_sequence_plan_path(runtime_root: Path | None = None) -> Path:
    return organism_data_dir(runtime_root) / "supervised_sequence_plan.json"


def autonomous_gap_closer_path(runtime_root: Path | None = None) -> Path:
    return organism_data_dir(runtime_root) / "autonomous_gap_closer.json"


def autonomous_next_steps_path(runtime_root: Path | None = None) -> Path:
    return organism_data_dir(runtime_root) / "autonomous_next_steps.json"


def autonomous_progress_delta_path(runtime_root: Path | None = None) -> Path:
    return organism_data_dir(runtime_root) / "autonomous_progress_delta.json"


def autonomous_gap_closer_previous_path(runtime_root: Path | None = None) -> Path:
    return organism_data_dir(runtime_root) / "autonomous_gap_closer.previous.json"


def first_supervised_trade_command_center_path(runtime_root: Path | None = None) -> Path:
    return organism_data_dir(runtime_root) / "first_supervised_trade_command_center.json"


def first_supervised_trade_runbook_path(runtime_root: Path | None = None) -> Path:
    return organism_data_dir(runtime_root) / "first_supervised_trade_runbook.md"


def gate_b_readiness_report_path(runtime_root: Path | None = None) -> Path:
    return organism_data_dir(runtime_root) / "gate_b_readiness_report.json"


def daily_marchboard_path(runtime_root: Path | None = None) -> Path:
    return organism_data_dir(runtime_root) / "daily_marchboard.json"


def weekly_marchboard_path(runtime_root: Path | None = None) -> Path:
    return organism_data_dir(runtime_root) / "weekly_marchboard.json"
