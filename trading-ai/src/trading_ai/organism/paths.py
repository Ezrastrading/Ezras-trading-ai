"""Filesystem layout for ACCO under the Trade Intelligence databank root."""

from __future__ import annotations

from pathlib import Path

from trading_ai.nte.databank.local_trade_store import databank_memory_root


def organism_dir() -> Path:
    p = databank_memory_root() / "organism"
    p.mkdir(parents=True, exist_ok=True)
    return p


def meta_learning_path() -> Path:
    return organism_dir() / "meta_learning.json"


def failsafe_state_path() -> Path:
    return organism_dir() / "failsafe_state.json"


def operating_mode_path() -> Path:
    return organism_dir() / "operating_mode.json"
