"""
Polymarket-focused hunts (8–12): crypto scalp, pure arb, near resolution, order-book imbalance, volume spike.

BTC spot from Binance public API (10s cache). Uses ``scipy.stats`` for short-horizon log-normal CDF.
"""

from __future__ import annotations

import logging
import math
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Dict, List, Optional, Set

import requests

from trading_ai.shark.models import HuntSignal, HuntType, MarketSnapshot

logger = logging.getLogger(__name__)

_diag_market_index: ContextVar[Optional[int]] = ContextVar("_diag_market_index", default=None)


@contextmanager
def hunt_diagnostic_context(market_index: Optional[int]):
    """When ``market_index`` is 0..9, near-resolution / arb hunt checks log at INFO."""
    token = _diag_market_index.set(market_index)
    try:
        yield
    finally:
        _diag_market_index.reset(token)


def _hunt_diag_use_info() -> bool:
    i = _diag_market_index.get()
    return i is not None and 0 <= int(i) < 10

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
    """True when ``end_date_seconds`` is set and resolution is within 60 minutes (any category)."""
    end = getattr(snapshot, "end_date_seconds", None)
    if end is None:
        return False
    minutes_left = (float(end) - time.time()) / 60.0
    return 0 <= minutes_left <= 60


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
    if (m.outlet or "").lower() != "polymarket":
        return None
    if not _is_short_resolution_market(m):
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
    if (m.outlet or "").lower() not in ("polymarket", "kalshi"):
        return None
    yes = m.yes_price
    no = m.no_price
    _log = logger.info if _hunt_diag_use_info() else logger.debug
    _log(
        "arb check: yes=%s no=%s total=%.3f",
        yes,
        no,
        float(yes) + float(no),
    )
    total_cost = yes + no
    if total_cost >= 0.99:
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
    if (m.outlet or "").lower() not in ("polymarket", "kalshi"):
        return None
    yes = m.yes_price
    no = m.no_price
    _log = logger.info if _hunt_diag_use_info() else logger.debug
    _log(
        "near_res check: yes=%s no=%s end=%s market=%s",
        yes,
        no,
        getattr(m, "end_date_seconds", None),
        str(m.market_id)[:20],
    )
    now = time.time()
    end = _end_seconds(m)
    if end is None:
        minutes_left = m.time_to_resolution_seconds / 60.0
    else:
        minutes_left = (end - now) / 60.0
    if minutes_left > 30 or minutes_left < 0:
        return None
    _nr_tiers = ((0.97, 0.75, "T1"), (0.93, 0.50, "T2"), (0.90, 0.30, "T3"))
    for thr, stake_frac, tier in _nr_tiers:
        if yes >= thr:
            edge = 1.0 - yes
            return HuntSignal(
                HuntType.NEAR_RESOLUTION,
                edge_after_fees=max(edge, 1e-6),
                confidence=float(yes),
                details={
                    "side": "yes",
                    "minutes_left": minutes_left,
                    "stake_fraction": stake_frac,
                    "tier": tier,
                    "reasoning": f"{tier} YES={yes:.2f} resolves_in={minutes_left:.1f}min stake={stake_frac:.0%}",
                },
            )
    for thr, stake_frac, tier in _nr_tiers:
        if no >= thr:
            edge = 1.0 - no
            return HuntSignal(
                HuntType.NEAR_RESOLUTION,
                edge_after_fees=max(edge, 1e-6),
                confidence=float(no),
                details={
                    "side": "no",
                    "minutes_left": minutes_left,
                    "stake_fraction": stake_frac,
                    "tier": tier,
                    "reasoning": f"{tier} NO={no:.2f} resolves_in={minutes_left:.1f}min stake={stake_frac:.0%}",
                },
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
    price_history: Optional[Dict[str, List[float]]] = None,
) -> List[HuntSignal]:
    """Run only selected :class:`HuntType` runners (fast scans). Polymarket + Kalshi + Manifold (hunts no-op when inapplicable)."""
    from trading_ai.shark.kalshi_hunts import (
        hunt_kalshi_metaculus_agreement,
        hunt_kalshi_metaculus_divergence,
        hunt_kalshi_momentum,
        hunt_kalshi_near_close,
        hunt_kalshi_polymarket_divergence,
        hunt_near_resolution_hv,
    )

    o = (m.outlet or "").lower()
    if o not in ("polymarket", "kalshi", "manifold"):
        return []
    mapping = {
        HuntType.CRYPTO_SCALP: hunt_crypto_scalp,
        HuntType.PURE_ARBITRAGE: hunt_pure_arbitrage,
        HuntType.NEAR_RESOLUTION: hunt_near_resolution,
        HuntType.NEAR_RESOLUTION_HV: hunt_near_resolution_hv,
        HuntType.ORDER_BOOK_IMBALANCE: hunt_order_book_imbalance,
        HuntType.VOLUME_SPIKE: hunt_volume_spike,
        HuntType.KALSHI_NEAR_CLOSE: hunt_kalshi_near_close,
        HuntType.KALSHI_CONVERGENCE: hunt_kalshi_polymarket_divergence,
        HuntType.KALSHI_METACULUS_DIVERGE: hunt_kalshi_metaculus_divergence,
        HuntType.KALSHI_METACULUS_AGREE: hunt_kalshi_metaculus_agreement,
    }
    poly_only = {HuntType.CRYPTO_SCALP, HuntType.ORDER_BOOK_IMBALANCE}
    sigs: List[HuntSignal] = []
    for ht in hunt_types:
        if ht in poly_only and o != "polymarket":
            continue
        if ht == HuntType.KALSHI_MOMENTUM:
            if o != "kalshi":
                continue
            try:
                r = hunt_kalshi_momentum(m, price_history=price_history or {})
            except Exception:
                logger.exception("filtered hunt KALSHI_MOMENTUM failed")
                continue
            if r:
                sigs.append(r)
            continue
        fn = mapping.get(ht)
        if not fn:
            continue
        if ht == HuntType.NEAR_RESOLUTION_HV and o not in ("kalshi", "manifold"):
            continue
        if ht in (
            HuntType.KALSHI_NEAR_CLOSE,
            HuntType.KALSHI_CONVERGENCE,
            HuntType.KALSHI_METACULUS_DIVERGE,
            HuntType.KALSHI_METACULUS_AGREE,
        ) and o != "kalshi":
            continue
        try:
            r = fn(m)
        except Exception:
            logger.exception("filtered hunt %s failed", ht)
            continue
        if r:
            sigs.append(r)
    return sigs
