"""Filesystem paths for edge registry (under databank root)."""

from __future__ import annotations

from pathlib import Path

from trading_ai.nte.databank.local_trade_store import databank_memory_root


def edge_registry_path() -> Path:
    return databank_memory_root() / "edge_registry.json"


def edge_feedback_log_path() -> Path:
    return databank_memory_root() / "edge_feedback.jsonl"
