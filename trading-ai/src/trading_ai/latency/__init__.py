"""Latency-oriented signal detection (Upside layer — does not bypass safety gates)."""

from trading_ai.latency.latency_engine import (
    LATENCY_MAX_HOLD_SECONDS,
    LATENCY_TRADE_PRIORITY,
    LatencySignal,
    build_market_snapshot_for_latency,
    detect_latency_signal,
)

__all__ = [
    "LATENCY_MAX_HOLD_SECONDS",
    "LATENCY_TRADE_PRIORITY",
    "LatencySignal",
    "build_market_snapshot_for_latency",
    "detect_latency_signal",
]
