"""Paths for trader-visible artifacts under ``data/trade_logs`` (same root as raw logs)."""

from __future__ import annotations

from pathlib import Path

from trading_ai.reality.paths import trade_logs_dir


def trades_clean_csv_path() -> Path:
    return trade_logs_dir() / "trades_clean.csv"


def daily_summary_json_path() -> Path:
    return trade_logs_dir() / "daily_summary.json"


def daily_summary_txt_path() -> Path:
    return trade_logs_dir() / "daily_summary.txt"


def weekly_summary_json_path() -> Path:
    return trade_logs_dir() / "weekly_summary.json"


def weekly_summary_txt_path() -> Path:
    return trade_logs_dir() / "weekly_summary.txt"
