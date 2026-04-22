"""Paths for crypto intelligence artifacts under ``EZRAS_RUNTIME_ROOT``."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from trading_ai.runtime_paths import ezras_runtime_root


def crypto_intel_root(runtime_root: Optional[Path] = None) -> Path:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    p = root / "data" / "learning" / "crypto_intelligence"
    p.mkdir(parents=True, exist_ok=True)
    return p


def candidate_events_jsonl_path(runtime_root: Optional[Path] = None) -> Path:
    return crypto_intel_root(runtime_root) / "candidate_events.jsonl"


def rejection_events_jsonl_path(runtime_root: Optional[Path] = None) -> Path:
    return crypto_intel_root(runtime_root) / "rejection_events.jsonl"


def trade_outcome_link_jsonl_path(runtime_root: Optional[Path] = None) -> Path:
    """Links trade_id/order_id to candidate/setup context when available."""
    return crypto_intel_root(runtime_root) / "trade_outcome_links.jsonl"


def setup_family_stats_json_path(runtime_root: Optional[Path] = None) -> Path:
    return crypto_intel_root(runtime_root) / "setup_family_stats.json"


def daily_distillation_json_path(runtime_root: Optional[Path] = None) -> Path:
    return crypto_intel_root(runtime_root) / "daily_distillation.json"

