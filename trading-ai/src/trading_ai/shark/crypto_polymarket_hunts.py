"""
Polymarket-focused hunts (8–12): crypto scalp, pure arb, near resolution, order-book imbalance, volume spike.

BTC spot from Binance public API (10s cache). Uses ``scipy.stats`` for short-horizon log-normal CDF.
"""

from __future__ import annotations

import logging
import math
import re
import time
from typing import List, Optional, Set

import requests

from trading_ai.shark.models import HuntSignal, HuntType, MarketSnapshot

logger = logging.getLogger(__name__)

_BTC_CACHE: tuple[float, float] = (0.0, 0.0)  # (price, ts)
_BTC_CACHE_TTL = 10.0


def _market_question(m: MarketSnapshot) -> str:
    q = getattr(m, "question_text", None) or ""
    if isinstance(q, str) and q.strip():
        return q.strip()
    return (m.resolution_criteria or "").strip()


def _end_timestamp(m: MarketSnapshot) -> Optional[float]:
    ts = getattr(m, "end_timestamp_unix", None)
    if ts is not None and ts > 0:
        return float(ts)
    return None


def _end_seconds(m: MarketSnapshot) -> Optional[float]:
    """Absolute resolution time (unix seconds), if known."""
    ed = getattr(m, "end_date_seconds", None)
    if ed is not None and float(ed) > 0:
        return float(ed)
    return _end_timestamp(m)


def _is_short_resolution_market(snapshot: MarketSnapshot) -> bool:
    """True when the market resolves within ~60 minutes (any category / outlet)."""
    now = time.time()
    end = _end_seconds(snapshot)
    if end is not None:
        minutes_left = (end - now) / 60.0
        return 0 <= minutes_left <= 60
    ttr = float(snapshot.time_to_resolution_seconds or 0.0)
    if ttr <= 0:
        return False
    return (ttr / 60.0) <= 60.0


def _is_crypto_short_market(snapshot: MarketSnapshot) -> bool:
    """Polymarket BTC/ETH strike markets resolving within ~30 minutes (crypto scalp only)."""
    if (snapshot.outlet or "").lower() != "polymarket":
        return False
    q = _market_question(snapshot).lower()
    if not q:
        return False
    crypto = (
        "bitcoin" in q
        or "btc" in q
        or "ethereum" in q
        or bool(re.search(r"\beth\b", q))
    )
    strike_q = "above" in q or "below" in q
    end = _end_seconds(snapshot)
    now = time.time()
    if end is not None:
        return crypto and strike_q and (end - now) < 1800
    ttr = snapshot.time_to_resolution_seconds
    return crypto and strike_q and ttr < 1800


def get_btc_price_usd() -> Optional[float]:
    """Spot BTC/USDT from Binance; cached ~10s."""
    global _BTC_CACHE
    now = time.time()
    if _BTC_CACHE[1] and (now - _BTC_CACHE[1]) < _BTC_CACHE_TTL:
        return _BTC_CACHE[0]
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/ticker/price",
            params={"symbol": "BTCUSDT"},
            timeout=5,
        )
        r.raise_for_status()
        px = float(r.json().get("price", 0) or 0)
        if px <= 0:
            return None
        _BTC_CACHE = (px, now)
        return px
    except Exception as exc:
        logger.debug("Binance BTC price fetch failed: %s", exc)
        return None


def parse_strike_from_question(question: str) -> Optional[float]:
    """Extract dollar strike e.g. ``Will BTC be above $85,000`` → 85000.0."""
    if not question:
        return None
    m = re.search(r"\$\s*([\d,]+(?:\.\d+)?)", question)
    if m:
        raw = m.group(1).replace(",", "")
        try:
            return float(raw)
        except ValueError:
            return None
    m2 = re.search(r"([\d,]+(?:\.\d+)?)\s*USD", question, re.I)
    if m2:
        try:
            return float(m2.group(1).replace(",", ""))
        except ValueError:
            return None
    return None


def calc_crypto_prob(
    current_price: float,
    strike: float,
    minutes_to_resolve: float,
) -> float:
    """Short-horizon log-normal style prob(price > strike) for BTC."""
    try:
        from scipy import stats
    except ImportError as e:
        raise ImportError("calc_crypto_prob requires scipy") from e
    if current_price <= 0 or strike <= 0 or minutes_to_resolve <= 0:
        return 0.5
    vol_per_min = 0.003 / math.sqrt(60.0)
    total_vol = vol_per_min * math.sqrt(max(minutes_to_resolve, 1e-6))
    if total_vol <= 0:
        return 0.5
    log_ret = math.log(strike / current_price)
    prob_above = 1.0 - float(stats.norm.cdf(log_ret / total_vol))
    return max(0.01, min(0.99, prob_above))


def hunt_crypto_scalp(m: MarketSnapshot) -> Optional[HuntSignal]:
    if not _is_crypto_short_market(m):
        return None
    btc = get_btc_price_usd()
    if btc is None:
        return None
    q = _market_question(m)
    strike = parse_strike_from_question(q)
    if strike is None:
        return None
    end = _end_seconds(m)
    now = time.time()
    if end is not None:
        minutes_left = max((end - now) / 60.0, 0.25)
    else:
        minutes_left = max(m.time_to_resolution_seconds / 60.0, 0.25)
    try:
        true_prob = calc_crypto_prob(btc, strike, minutes_left)
    except ImportError:
        return None
    market_prob = m.yes_price
    edge = true_prob - market_prob
    if abs(edge) < 0.02:
        return None
    side = "yes" if edge > 0 else "no"
    return HuntSignal(
        HuntType.CRYPTO_SCALP,
        edge_after_fees=abs(edge),
        confidence=0.75,
        details={
            "side": side,
            "btc_spot": btc,
            "strike": strike,
            "true_prob": true_prob,
            "market_prob": market_prob,
            "minutes_left": minutes_left,
            "reasoning": f"BTC={btc} strike={strike} true={true_prob:.3f} mkt={market_prob:.3f}",
        },
    )


def hunt_pure_arbitrage(m: MarketSnapshot) -> Optional[HuntSignal]:
    yes = m.yes_price
    no = m.no_price
    total_cost = yes + no
    if total_cost > 0.99:
        return None
    edge = 1.0 - total_cost
    return HuntSignal(
        HuntType.PURE_ARBITRAGE,
        edge_after_fees=edge,
        confidence=0.99,
        details={
            "side": "both",
            "yes_price": yes,
            "no_price": no,
            "total_cost": total_cost,
            "reasoning": f"yes={yes} no={no} total={total_cost:.3f} edge={edge:.3f}",
        },
    )


def hunt_near_resolution(m: MarketSnapshot) -> Optional[HuntSignal]:
    if not _is_short_resolution_market(m):
        return None
    yes = m.yes_price
    no = m.no_price
    now = time.time()
    end = _end_seconds(m)
    if end is None:
        minutes_left = m.time_to_resolution_seconds / 60.0
    else:
        minutes_left = (end - now) / 60.0
    if yes >= 0.93:
        edge = 1.0 - yes
        return HuntSignal(
            HuntType.NEAR_RESOLUTION,
            edge_after_fees=max(edge, 1e-6),
            confidence=0.95,
            details={"side": "yes", "minutes_left": minutes_left, "reasoning": f"YES={yes} resolves_in={minutes_left:.1f}min"},
        )
    if no >= 0.93:
        edge = 1.0 - no
        return HuntSignal(
            HuntType.NEAR_RESOLUTION,
            edge_after_fees=max(edge, 1e-6),
            confidence=0.95,
            details={"side": "no", "minutes_left": minutes_left, "reasoning": f"NO={no} resolves_in={minutes_left:.1f}min"},
        )
    return None


def hunt_order_book_imbalance(m: MarketSnapshot) -> Optional[HuntSignal]:
    if (m.outlet or "").lower() != "polymarket":
        return None
    y = getattr(m, "best_ask_yes", None)
    n = getattr(m, "best_ask_no", None)
    if y is None or n is None or (y + n) <= 0:
        return None
    yes_l = float(y)
    no_l = float(n)
    denom = yes_l + no_l
    ratio = yes_l / denom
    if ratio < 0.30:
        edge = 0.30 - ratio
        return HuntSignal(
            HuntType.ORDER_BOOK_IMBALANCE,
            edge_after_fees=edge,
            confidence=0.65,
            details={"side": "yes", "ratio": ratio, "reasoning": f"YES liquidity thin ratio={ratio:.2f}"},
        )
    if ratio > 0.70:
        edge = ratio - 0.70
        return HuntSignal(
            HuntType.ORDER_BOOK_IMBALANCE,
            edge_after_fees=edge,
            confidence=0.65,
            details={"side": "no", "ratio": ratio, "reasoning": f"NO liquidity thin ratio={ratio:.2f}"},
        )
    return None


def hunt_volume_spike(m: MarketSnapshot) -> Optional[HuntSignal]:
    volume = float(getattr(m, "volume_24h", 0) or 0)
    if volume < 5000:
        return None
    yes = m.yes_price
    no = m.no_price
    if yes is None or no is None:
        return None
    if 0.35 <= yes <= 0.65:
        edge = abs(yes - 0.50) + 0.03
        side = "yes" if yes < 0.50 else "no"
        return HuntSignal(
            HuntType.VOLUME_SPIKE,
            edge_after_fees=edge,
            confidence=0.60,
            details={
                "side": side,
                "reasoning": f"High volume ${volume:.0f} contested market",
            },
        )
    return None


_POLY_STRATEGY_FUNCS = (
    hunt_pure_arbitrage,
    hunt_near_resolution,
    hunt_order_book_imbalance,
    hunt_volume_spike,
    hunt_crypto_scalp,
)


def append_polymarket_strategy_hunts(m: MarketSnapshot, *, now: Optional[float] = None) -> List[HuntSignal]:
    """Runs hunts 8–12 (Polymarket + cross-outlet arb/near/volume). Caller merges into ``sigs``."""
    out: List[HuntSignal] = []
    for fn in _POLY_STRATEGY_FUNCS:
        try:
            r = fn(m)
        except Exception:
            logger.exception("polymarket strategy hunt %s failed", fn.__name__)
            continue
        if r:
            out.append(r)
    return out


def run_filtered_polymarket_hunts(
    m: MarketSnapshot,
    hunt_types: Set[HuntType],
    *,
    now: Optional[float] = None,
) -> List[HuntSignal]:
    """Run only selected :class:`HuntType` runners (fast scans). Polymarket + Kalshi where applicable."""
    o = (m.outlet or "").lower()
    if o not in ("polymarket", "kalshi"):
        return []
    mapping = {
        HuntType.CRYPTO_SCALP: hunt_crypto_scalp,
        HuntType.PURE_ARBITRAGE: hunt_pure_arbitrage,
        HuntType.NEAR_RESOLUTION: hunt_near_resolution,
        HuntType.ORDER_BOOK_IMBALANCE: hunt_order_book_imbalance,
        HuntType.VOLUME_SPIKE: hunt_volume_spike,
    }
    poly_only = {HuntType.CRYPTO_SCALP, HuntType.ORDER_BOOK_IMBALANCE}
    sigs: List[HuntSignal] = []
    for ht in hunt_types:
        if ht in poly_only and o != "polymarket":
            continue
        fn = mapping.get(ht)
        if not fn:
            continue
        try:
            r = fn(m)
        except Exception:
            logger.exception("filtered hunt %s failed", ht)
            continue
        if r:
            sigs.append(r)
    return sigs
