"""Filesystem layout for reality validation + trade truth logs."""

from __future__ import annotations

import os
from pathlib import Path

from trading_ai.runtime_paths import ezras_runtime_root


def trade_logs_dir() -> Path:
    """
    Append-only trade logs: ``data/trade_logs`` under runtime root unless overridden.

    Override: ``TRADE_LOGS_DIR`` (absolute path to directory).
    """
    override = (os.environ.get("TRADE_LOGS_DIR") or "").strip()
    if override:
        p = Path(override).expanduser().resolve()
    else:
        p = ezras_runtime_root() / "data" / "trade_logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def reality_data_dir() -> Path:
    """
    Persistence for execution truth, edge truth, discipline logs.

    Override: ``REALITY_DATA_DIR``; default ``<EZRAS_RUNTIME_ROOT>/data/reality``.
    """
    override = (os.environ.get("REALITY_DATA_DIR") or "").strip()
    if override:
        p = Path(override).expanduser().resolve()
    else:
        p = ezras_runtime_root() / "data" / "reality"
    p.mkdir(parents=True, exist_ok=True)
    return p
