"""Filesystem layout for daily review artifacts under ``EZRAS_RUNTIME_ROOT/data/review``."""

from __future__ import annotations

from pathlib import Path

from trading_ai.runtime_paths import ezras_runtime_root


def review_data_dir() -> Path:
    p = ezras_runtime_root() / "data" / "review"
    p.mkdir(parents=True, exist_ok=True)
    return p


def daily_diagnosis_path() -> Path:
    return review_data_dir() / "daily_diagnosis.json"


def ceo_daily_review_json_path() -> Path:
    return review_data_dir() / "ceo_daily_review.json"


def ceo_daily_review_txt_path() -> Path:
    return review_data_dir() / "ceo_daily_review.txt"


def external_context_override_path() -> Path:
    return review_data_dir() / "external_context_override.json"
