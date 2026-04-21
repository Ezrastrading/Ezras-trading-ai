"""
Market family classification and scalpability filters for S&P / BTC / ETH Kalshi series.

Uses REST snapshot fields (``*_dollars``, ``*_fp``) and order book levels; no hold-to-settlement logic.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from trading_ai.shark.kalshi_scalp_config import KalshiScalpConfig
from trading_ai.shark.outlets.kalshi import (
    _kalshi_market_tradeable_core,
    _kalshi_market_volume,
    _parse_close_timestamp_unix,
)


class MarketFamily(str, Enum):
    SP = "S&P"
    BTC = "BTC"
    ETH = "ETH"


_TERMINAL = frozenset({"closed", "settled", "finalized", "determined", "expired", "cancelled", "canceled"})


def _root_prefix(ticker: str) -> str:
    t = (ticker or "").strip().upper()
    if "-" in t:
        return t.split("-", 1)[0]
    return t


def classify_market_family(ticker: str, series_ticker: str = "", cfg: Optional[KalshiScalpConfig] = None) -> Optional[MarketFamily]:
    """Assign S&P, BTC, or ETH from ticker / series (longest series prefix wins)."""
    cfg = cfg or KalshiScalpConfig()
    root = _root_prefix(ticker)
    st = (series_ticker or "").strip().upper()
    candidates = [root, st] if st else [root]

    def longest_match(prefixes: Tuple[str, ...]) -> bool:
        for p in sorted(prefixes, key=len, reverse=True):
            for c in candidates:
                if c.startswith(p):
                    return True
        return False

    if longest_match(tuple(cfg.sp_series_tickers)):
        return MarketFamily.SP
    if longest_match(tuple(cfg.btc_series_tickers)):
        return MarketFamily.BTC
    if longest_match(tuple(cfg.eth_series_tickers)):
        return MarketFamily.ETH
    return None


def _best_price_on_levels(levels: Any, *, bid: bool) -> Optional[float]:
    """Levels: list of [price_cents, size] or dicts; return best bid (max cents) or best ask (min cents)."""
    if not isinstance(levels, list) or not levels:
        return None
    best: Optional[float] = None
    for lv in levels:
        if isinstance(lv, (list, tuple)) and len(lv) >= 1:
            c = float(lv[0])
        elif isinstance(lv, dict):
            c = float(lv.get("price", lv.get("price_cents", 0)) or 0)
        else:
            continue
        if c <= 0:
            continue
        if best is None:
            best = c
        elif bid:
            if c > best:
                best = c
        else:
            if c < best:
                best = c
    return best


def _top_size_on_levels(levels: Any, *, bid: bool) -> float:
    """Size at the best bid (max price) or best ask (min price) level."""
    if not isinstance(levels, list) or not levels:
        return 0.0
    best_p = _best_price_on_levels(levels, bid=bid)
    if best_p is None:
        return 0.0
    for lv in levels:
        if isinstance(lv, (list, tuple)) and len(lv) >= 2:
            p, sz = float(lv[0]), float(lv[1])
        elif isinstance(lv, dict):
            p = float(lv.get("price", lv.get("price_cents", 0)) or 0)
            sz = float(lv.get("size") or lv.get("count") or lv.get("count_fp") or 0)
        else:
            continue
        if abs(p - best_p) < 1e-6:
            return max(0.0, sz)
    return 0.0


def parse_orderbook_yes_no_best_bid_ask_cents(ob_root: Dict[str, Any]) -> Tuple[Optional[int], Optional[int], Optional[int], Optional[int]]:
    """Return yes_bid, yes_ask, no_bid, no_ask in cents (1–99) from GET …/orderbook JSON."""
    ob = ob_root.get("orderbook") if isinstance(ob_root.get("orderbook"), dict) else ob_root
    if not isinstance(ob, dict):
        return None, None, None, None
    yb = _best_price_on_levels(ob.get("yes"), bid=True)
    ya = _best_price_on_levels(ob.get("yes"), bid=False)
    nb = _best_price_on_levels(ob.get("no"), bid=True)
    na = _best_price_on_levels(ob.get("no"), bid=False)

    def clip(ci: Optional[float]) -> Optional[int]:
        if ci is None:
            return None
        v = int(round(ci))
        return max(1, min(99, v))

    return clip(yb), clip(ya), clip(nb), clip(na)


def orderbook_depth_at_best(ob_root: Dict[str, Any]) -> Tuple[float, float, float, float]:
    """Top-of-book sizes on yes bid/ask and no bid/ask."""
    ob = ob_root.get("orderbook") if isinstance(ob_root.get("orderbook"), dict) else ob_root
    if not isinstance(ob, dict):
        return 0.0, 0.0, 0.0, 0.0
    yes = ob.get("yes") or []
    no = ob.get("no") or []
    return (
        _top_size_on_levels(yes, bid=True),
        _top_size_on_levels(yes, bid=False),
        _top_size_on_levels(no, bid=True),
        _top_size_on_levels(no, bid=False),
    )


def kalshi_scalar_to_probability(val: Any) -> Optional[float]:
    """Normalize REST scalars (0–1, 1–99 cents, or fixed-point) to probability."""
    if val is None:
        return None
    try:
        v = float(str(val).strip())
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    if v <= 1.0:
        return min(0.99, max(0.01, v))
    if v <= 100.0:
        return min(0.99, max(0.01, v / 100.0))
    if v <= 10_000.0:
        return min(0.99, max(0.01, v / 10_000.0))
    return min(0.99, max(0.01, v / 1_000_000.0))


def normalize_fill_price_to_probability(raw: Any) -> float:
    """Order fill / avg price fields → probability for PnL (best-effort across API variants)."""
    p = kalshi_scalar_to_probability(raw)
    return float(p) if p is not None else 0.0


@dataclass
class LiquiditySnapshot:
    yes_bid_cents: Optional[int]
    yes_ask_cents: Optional[int]
    no_bid_cents: Optional[int]
    no_ask_cents: Optional[int]
    yes_bid_sz: float
    yes_ask_sz: float
    no_bid_sz: float
    no_ask_sz: float

    def spread_cents_yes(self) -> Optional[float]:
        if self.yes_bid_cents is None or self.yes_ask_cents is None:
            return None
        return float(self.yes_ask_cents - self.yes_bid_cents)

    def spread_cents_no(self) -> Optional[float]:
        if self.no_bid_cents is None or self.no_ask_cents is None:
            return None
        return float(self.no_ask_cents - self.no_bid_cents)

    def exit_size_for_side(self, side: str) -> float:
        s = side.lower()
        if s == "yes":
            return self.yes_bid_sz
        return self.no_bid_sz


@dataclass
class FilterResult:
    ok: bool
    reason: str
    family: Optional[MarketFamily] = None
    liquidity: Optional[LiquiditySnapshot] = None


def is_market_stale_or_inactive(m: Dict[str, Any], now: float) -> bool:
    """True if closed, settled, or past scheduled close."""
    st = str(m.get("status", "")).strip().lower()
    if st in _TERMINAL:
        return True
    if m.get("settled") or m.get("is_settled"):
        return True
    end = _parse_close_timestamp_unix(m)
    if end is not None and end <= now:
        return True
    return False


def evaluate_scalp_filter(
    m: Dict[str, Any],
    orderbook: Dict[str, Any],
    *,
    cfg: KalshiScalpConfig,
    now: Optional[float] = None,
) -> FilterResult:
    """
    Reject illiquid / wide-spread / stale markets unsuitable for a small dollar scalp.

    Exitability: require minimum resting size on the bid of the chosen side at parse time.
    """
    tnow = time.time() if now is None else float(now)
    ticker = str(m.get("ticker") or "")
    series = str(m.get("series_ticker") or "")
    fam = classify_market_family(ticker, series, cfg)
    if fam is None:
        return FilterResult(False, "family_not_sp_btc_eth", None, None)

    if fam.value not in {x.strip() for x in cfg.allowed_market_families}:
        return FilterResult(False, "family_not_allowed", fam, None)

    if not _kalshi_market_tradeable_core(m, tnow):
        return FilterResult(False, "not_tradeable_core", fam, None)

    if is_market_stale_or_inactive(m, tnow):
        return FilterResult(False, "stale_or_inactive", fam, None)

    vol = _kalshi_market_volume(m)
    if vol < cfg.min_volume_fp:
        return FilterResult(False, f"volume_low_{vol:.2f}", fam, None)

    yb, ya, nb, na = parse_orderbook_yes_no_best_bid_ask_cents(orderbook)
    ybs, yas, nbs, nas = orderbook_depth_at_best(orderbook)
    liq = LiquiditySnapshot(yb, ya, nb, na, ybs, yas, nbs, nas)

    if yb is None or ya is None or nb is None or na is None:
        return FilterResult(False, "missing_orderbook_quotes", fam, liq)

    sy = liq.spread_cents_yes()
    sn = liq.spread_cents_no()
    if sy is None or sn is None:
        return FilterResult(False, "spread_unknown", fam, liq)

    max_spread_cents = cfg.max_spread_prob * 100.0
    if min(sy, sn) > max_spread_cents:
        return FilterResult(False, f"spread_wide_yes={sy:.1f}c_no={sn:.1f}c", fam, liq)

    if min(ybs, yas, nbs, nas) < cfg.min_top_of_book_contracts:
        return FilterResult(False, "book_too_thin", fam, liq)

    return FilterResult(True, "ok", fam, liq)


def pick_scalp_side(liq: LiquiditySnapshot) -> str:
    """Prefer the tighter-spread side for entry (symmetric scalp)."""
    sy = liq.spread_cents_yes() or 999.0
    sn = liq.spread_cents_no() or 999.0
    return "yes" if sy <= sn else "no"


def entry_side_price_cents(liq: LiquiditySnapshot, side: str) -> Optional[int]:
    """Aggressive buy: pay best ask on that side."""
    if side.lower() == "yes":
        return liq.yes_ask_cents
    return liq.no_ask_cents


def mark_price_cents_for_pnl(side: str, liq: LiquiditySnapshot) -> Optional[int]:
    """Mark-to-exit for long side: best bid (what you can sell into)."""
    if side.lower() == "yes":
        return liq.yes_bid_cents
    return liq.no_bid_cents


def unrealized_pnl_usd(
    side: str,
    entry_prob: float,
    mark_prob: float,
    contracts: float,
) -> float:
    """Binary long PnL in dollars: (mark - entry) * contracts (each contract $1 face)."""
    return (mark_prob - entry_prob) * float(contracts)
