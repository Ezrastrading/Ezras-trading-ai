"""Canonical wiring from execution / post-trade paths into tickets / learning (non-blocking)."""

from trading_ai.intelligence.integration.live_hooks import record_post_trade_hub_event, record_shark_submit_outcome

__all__ = ["record_post_trade_hub_event", "record_shark_submit_outcome"]
