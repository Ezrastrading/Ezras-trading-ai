"""Phase 2 risk defaults — minimal subset for Telegram formatting."""

from __future__ import annotations

from types import SimpleNamespace

# Aligns with full Ezras default max 5% of account per trade.
DEFAULT_PHASE2_RISK = SimpleNamespace(max_pct_per_trade=0.05)
