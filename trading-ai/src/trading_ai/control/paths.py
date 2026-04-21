"""Filesystem layout for command center snapshots under ``EZRAS_RUNTIME_ROOT/data/control``."""

from __future__ import annotations

from pathlib import Path

from trading_ai.runtime_paths import ezras_runtime_root


def control_data_dir() -> Path:
    p = ezras_runtime_root() / "data" / "control"
    p.mkdir(parents=True, exist_ok=True)
    return p


def command_center_snapshot_path() -> Path:
    return control_data_dir() / "command_center_snapshot.json"


def command_center_report_path() -> Path:
    return control_data_dir() / "command_center_report.txt"


def live_status_path() -> Path:
    return control_data_dir() / "live_status.txt"


def alerts_txt_path() -> Path:
    return control_data_dir() / "alerts.txt"


def equity_curve_csv_path() -> Path:
    return control_data_dir() / "equity_curve.csv"


def session_state_json_path() -> Path:
    return control_data_dir() / "session_state.json"


def trade_explanations_dir() -> Path:
    p = control_data_dir() / "trade_explanations"
    p.mkdir(parents=True, exist_ok=True)
    return p


def daily_trades_csv_path() -> Path:
    return control_data_dir() / "daily_trades.csv"


def daily_summary_operator_path() -> Path:
    return control_data_dir() / "daily_summary.txt"


def kill_switch_path() -> Path:
    return ezras_runtime_root() / "KILL_SWITCH"
