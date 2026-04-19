"""Execution mode: paper / replay / live — env-driven + full mode context for guards."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum


class ExecutionMode(str, Enum):
    PAPER = "paper"
    REPLAY = "replay"
    LIVE = "live"


def get_execution_mode() -> ExecutionMode:
    raw = (
        os.environ.get("NTE_EXECUTION_MODE") or os.environ.get("EZRAS_MODE") or "paper"
    ).strip().lower()
    if raw in ("live", "production", "prod"):
        return ExecutionMode.LIVE
    if raw in ("replay", "backtest"):
        return ExecutionMode.REPLAY
    return ExecutionMode.PAPER


def live_explicitly_enabled() -> bool:
    return (os.environ.get("NTE_LIVE_TRADING_ENABLED") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def live_orders_allowed() -> bool:
    """Live orders only if mode is live AND explicit flag set."""
    return get_execution_mode() == ExecutionMode.LIVE and live_explicitly_enabled()


@dataclass(frozen=True)
class ModeContext:
    """Snapshot of env + flags used for live-order decisions."""

    execution_mode: ExecutionMode
    nte_paper_mode: bool
    nte_dry_run: bool
    nte_live_trading_enabled: bool
    coinbase_enabled: bool
    execution_scope: str  # live | sandbox | research | paper
    strategy_live_ok: bool  # NTE_STRATEGY_LIVE_ALLOW
    dry_run: bool


def get_mode_context() -> ModeContext:
    paper = (os.environ.get("NTE_PAPER_MODE") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    dry = (os.environ.get("NTE_DRY_RUN") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    live_flag = (os.environ.get("NTE_LIVE_TRADING_ENABLED") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    cb = (os.environ.get("COINBASE_ENABLED") or "false").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    scope = (os.environ.get("NTE_EXECUTION_SCOPE") or "live").strip().lower()
    strat_live = (os.environ.get("NTE_STRATEGY_LIVE_ALLOW") or "true").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    return ModeContext(
        execution_mode=get_execution_mode(),
        nte_paper_mode=paper,
        nte_dry_run=dry,
        nte_live_trading_enabled=live_flag,
        coinbase_enabled=cb,
        execution_scope=scope,
        strategy_live_ok=strat_live,
        dry_run=dry,
    )
