"""Backward-compatible re-exports — canonical implementation: ``nte_global.capital_ledger``."""

from trading_ai.nte.nte_global.capital_ledger import (  # noqa: F401
    append_realized,
    load_ledger,
    net_equity_estimate,
    record_deposit,
    save_ledger,
    snapshot_for_goals,
    weekly_net_for_goals,
)

__all__ = [
    "append_realized",
    "load_ledger",
    "net_equity_estimate",
    "record_deposit",
    "save_ledger",
    "snapshot_for_goals",
    "weekly_net_for_goals",
]
