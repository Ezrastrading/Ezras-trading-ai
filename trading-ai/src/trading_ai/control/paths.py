"""Filesystem layout for control-plane artifacts under ``EZRAS_RUNTIME_ROOT`` (or an explicit runtime root)."""

from __future__ import annotations

from pathlib import Path

from trading_ai.runtime_paths import ezras_runtime_root


def control_data_dir(runtime_root: Path | None = None) -> Path:
    base = Path(runtime_root).resolve() if runtime_root is not None else Path(ezras_runtime_root()).resolve()
    p = base / "data" / "control"
    p.mkdir(parents=True, exist_ok=True)
    return p


def repo_packaged_control_defaults_dir() -> Path:
    """
    Packaged defaults under ``src/trading_ai/control/defaults``.

    Repo ``trading-ai/data`` is gitignored for runtime output; templates ship in ``src``.
    """
    here = Path(__file__).resolve()
    return here.parent / "defaults"


def command_center_snapshot_path(runtime_root: Path | None = None) -> Path:
    return control_data_dir(runtime_root) / "command_center_snapshot.json"


def command_center_report_path(runtime_root: Path | None = None) -> Path:
    return control_data_dir(runtime_root) / "command_center_report.txt"


def live_status_path(runtime_root: Path | None = None) -> Path:
    return control_data_dir(runtime_root) / "live_status.txt"


def alerts_txt_path(runtime_root: Path | None = None) -> Path:
    return control_data_dir(runtime_root) / "alerts.txt"


def equity_curve_csv_path(runtime_root: Path | None = None) -> Path:
    return control_data_dir(runtime_root) / "equity_curve.csv"


def session_state_json_path(runtime_root: Path | None = None) -> Path:
    return control_data_dir(runtime_root) / "session_state.json"


def trade_explanations_dir(runtime_root: Path | None = None) -> Path:
    p = control_data_dir(runtime_root) / "trade_explanations"
    p.mkdir(parents=True, exist_ok=True)
    return p


def daily_trades_csv_path(runtime_root: Path | None = None) -> Path:
    return control_data_dir(runtime_root) / "daily_trades.csv"


def daily_summary_operator_path(runtime_root: Path | None = None) -> Path:
    return control_data_dir(runtime_root) / "daily_summary.txt"


def kill_switch_path(runtime_root: Path | None = None) -> Path:
    base = Path(runtime_root).resolve() if runtime_root is not None else Path(ezras_runtime_root()).resolve()
    return base / "KILL_SWITCH"
