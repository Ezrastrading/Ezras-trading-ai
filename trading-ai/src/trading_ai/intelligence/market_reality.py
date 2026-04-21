"""
Market Reality Layer — spread and depth gates before trading illiquid or wide markets.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MAX_SPREAD_PCT = 0.002  # 0.2%
DEPTH_BAND_PCT = 0.005  # 0.5% from mid
LIQUIDITY_MULTIPLIER = 3.0  # need >= trade_size * 3


def _parse_levels(levels: Any) -> List[Tuple[float, float]]:
    """Return list of (price, size) from Coinbase-style levels or list of pairs."""
    out: List[Tuple[float, float]] = []
    if not levels:
        return out
    if isinstance(levels, dict):
        levels = levels.get("levels") or levels.get("bids") or levels.get("asks") or []
    for row in levels:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            try:
                p, s = float(row[0]), float(row[1])
            except (TypeError, ValueError):
                continue
            if p > 0 and s >= 0:
                out.append((p, s))
        elif isinstance(row, dict):
            try:
                p = float(row.get("price") or row.get("px") or 0.0)
                s = float(row.get("size") or row.get("sz") or row.get("quantity") or 0.0)
            except (TypeError, ValueError):
                continue
            if p > 0 and s >= 0:
                out.append((p, s))
    return out


def _depth_within_band(levels: List[Tuple[float, float]], mid: float, side: str) -> float:
    """Sum quote notional (price * size) for levels within DEPTH_BAND_PCT of mid."""
    if mid <= 0:
        return 0.0
    lo = mid * (1.0 - DEPTH_BAND_PCT)
    hi = mid * (1.0 + DEPTH_BAND_PCT)
    total = 0.0
    for price, size in levels:
        if lo <= price <= hi:
            total += price * size
    return total


def evaluate_market_conditions(orderbook: Any, trade_size: float) -> Dict[str, Any]:
    """
    Evaluate bid/ask quality and depth near mid.

    ``orderbook`` may be:
    - ``{"bids": [...], "asks": [...]}`` (lists of [price, size] or dicts)
    - ``None`` / empty → invalid (no assumed liquidity)

    Returns:
      {"valid": bool, "reason": str, "spread_pct": float, "liquidity": float}
    """
    ts = max(0.0, float(trade_size))
    bids = _parse_levels((orderbook or {}).get("bids") if isinstance(orderbook, dict) else None)
    asks = _parse_levels((orderbook or {}).get("asks") if isinstance(orderbook, dict) else None)

    if not bids or not asks:
        logger.info(
            "market_reality: decision=BLOCK spread_pct=n/a liquidity=0.0 reason=no_orderbook"
        )
        return {
            "valid": False,
            "reason": "no_orderbook",
            "spread_pct": float("nan"),
            "liquidity": 0.0,
        }

    best_bid = max(p for p, _ in bids)
    best_ask = min(p for p, _ in asks)
    if best_bid <= 0 or best_ask <= 0 or best_ask <= best_bid:
        logger.info(
            "market_reality: decision=BLOCK spread_pct=n/a liquidity=0.0 reason=crossed_or_bad_quotes"
        )
        return {
            "valid": False,
            "reason": "crossed_or_bad_quotes",
            "spread_pct": float("nan"),
            "liquidity": 0.0,
        }

    mid_price = (best_bid + best_ask) / 2.0
    spread = best_ask - best_bid
    spread_pct = spread / mid_price if mid_price > 0 else float("inf")

    liq_bid = _depth_within_band(bids, mid_price, "bid")
    liq_ask = _depth_within_band(asks, mid_price, "ask")
    liquidity = liq_bid + liq_ask

    need = ts * LIQUIDITY_MULTIPLIER
    valid = spread_pct <= MAX_SPREAD_PCT + 1e-12 and liquidity >= need - 1e-9

    if spread_pct > MAX_SPREAD_PCT:
        reason = "spread_too_wide"
    elif liquidity < need:
        reason = "insufficient_depth"
    else:
        reason = "ok"

    decision = "PASS" if valid else "BLOCK"
    sp_log = float(spread_pct) if spread_pct == spread_pct else -1.0  # NaN guard
    logger.info(
        "market_reality: decision=%s spread_pct=%.6f liquidity=%.4f required_depth=%.4f reason=%s",
        decision,
        sp_log,
        liquidity,
        need,
        reason,
    )

    return {
        "valid": bool(valid),
        "reason": reason,
        "spread_pct": float(spread_pct),
        "liquidity": float(liquidity),
    }


def orderbook_from_market_underlying(underlying: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Best-effort extract ``{"bids","asks"}`` from Shark ``underlying_data_if_available``."""
    if not underlying or not isinstance(underlying, dict):
        return None
    ob = underlying.get("orderbook")
    if isinstance(ob, dict) and ("bids" in ob or "asks" in ob):
        return {"bids": ob.get("bids") or [], "asks": ob.get("asks") or []}
    bids = underlying.get("bids") or underlying.get("bid_levels")
    asks = underlying.get("asks") or underlying.get("ask_levels")
    if bids is not None or asks is not None:
        return {"bids": bids or [], "asks": asks or []}
    return None
