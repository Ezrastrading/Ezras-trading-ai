"""Instrument and regime enums — edge lifecycle types live in :mod:`trading_ai.edge.models`."""

from __future__ import annotations

from enum import Enum


class InstrumentKind(str, Enum):
    SPOT = "spot"
    PREDICTION = "prediction"
    OPTIONS = "options"


class RegimeBucket(str, Enum):
    TREND = "TREND"
    RANGE = "RANGE"
    VOLATILE = "VOLATILE"
    LOW_LIQUIDITY = "LOW_LIQUIDITY"
    UNKNOWN = "UNKNOWN"
