"""Filesystem paths for production hardening artifacts."""

from __future__ import annotations

import os
from pathlib import Path

from trading_ai.runtime_paths import ezras_runtime_root


def _root() -> Path:
    return Path(os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()


def recent_order_ids_json() -> Path:
    return _root() / "data" / "control" / "recent_order_ids.json"


def price_tick_state_json() -> Path:
    return _root() / "data" / "control" / "price_tick_state.json"


def trade_ledger_jsonl() -> Path:
    return _root() / "data" / "control" / "trade_ledger.jsonl"
