"""Filesystem layout for continuous learning memory under ``EZRAS_RUNTIME_ROOT/data/learning``."""

from __future__ import annotations

from pathlib import Path

from trading_ai.runtime_paths import ezras_runtime_root


def learning_data_dir() -> Path:
    p = ezras_runtime_root() / "data" / "learning"
    p.mkdir(parents=True, exist_ok=True)
    return p


def trading_memory_path() -> Path:
    return learning_data_dir() / "trading_memory.json"


def improvement_history_path() -> Path:
    return learning_data_dir() / "improvement_history.jsonl"


def system_learning_log_path() -> Path:
    return learning_data_dir() / "system_learning_log.jsonl"


def self_learning_memory_path() -> Path:
    return learning_data_dir() / "self_learning_memory.json"


def last_48h_mastery_json_path() -> Path:
    return learning_data_dir() / "last_48h_system_mastery.json"


def last_48h_mastery_txt_path() -> Path:
    return learning_data_dir() / "last_48h_system_mastery.txt"


def system_mastery_report_json_path() -> Path:
    return learning_data_dir() / "system_mastery_report.json"


def system_mastery_report_txt_path() -> Path:
    return learning_data_dir() / "system_mastery_report.txt"


def weekly_learning_meta_path() -> Path:
    return learning_data_dir() / "weekly_learning_meta.json"


def daily_learning_marker_path() -> Path:
    return learning_data_dir() / "last_daily_learning_date.txt"


def ai_self_learning_sessions_dir() -> Path:
    p = ezras_runtime_root() / "data" / "review" / "ai_self_learning_sessions"
    p.mkdir(parents=True, exist_ok=True)
    return p
