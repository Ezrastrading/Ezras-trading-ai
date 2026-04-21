"""Market data + features for Coinbase BTC/ETH — spread, stability, liquidity, regime."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class FeatureSnapshot:
    product_id: str
    bid: float
    ask: float
    mid: float
    spread_pct: float
    quote_volume_24h: float
    stable: bool
    regime: str  # "range" | "trend_up" | "trend_down" | "unknown"
    ma20: float
    z_score: float


def _append_close(store: Any, product_id: str, mid: float, max_len: int = 120) -> List[float]:
    mm = store.load_json("market_memory.json")
    closes = mm.get("closes") or {}
    if not isinstance(closes, dict):
        closes = {}
    arr = closes.get(product_id)
    if not isinstance(arr, list):
        arr = []
    arr.append(float(mid))
    arr = arr[-max_len:]
    closes[product_id] = arr
    mm["closes"] = closes
    store.save_json("market_memory.json", mm)
    return arr


def _sma(xs: List[float], n: int) -> float:
    if len(xs) < n:
        return sum(xs) / len(xs) if xs else 0.0
    return sum(xs[-n:]) / float(n)


def _std(xs: List[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    v = sum((x - m) ** 2 for x in xs) / len(xs)
    return v ** 0.5


def compute_features(
    *,
    store: Any,
    client: Any,
    product_id: str,
    spike_block_pct: float,
    min_quote_volume_24h: float,
    bid_override: Optional[float] = None,
    ask_override: Optional[float] = None,
) -> Optional[FeatureSnapshot]:
    if bid_override is not None and ask_override is not None and bid_override > 0 and ask_override > 0:
        bid, ask = float(bid_override), float(ask_override)
    else:
        bid, ask = client.get_product_price(product_id)
    if bid <= 0 or ask <= 0:
        logger.debug("NTE: no bid/ask for %s", product_id)
        return None
    mid = (bid + ask) / 2.0
    spread_pct = (ask - bid) / mid if mid > 0 else 1.0

    vol24 = 0.0
    try:
        st = client.get_exchange_product_stats(product_id)
        if isinstance(st, dict):
            base_vol = float(st.get("volume") or 0)
            last = float(st.get("last") or mid)
            vol24 = base_vol * last if base_vol and last else 0.0
    except Exception:
        pass

    closes = _append_close(store, product_id, mid)
    ma20 = _sma(closes, 20)
    sd = _std(closes[-20:]) if len(closes) >= 2 else 0.0
    z_score = (mid - ma20) / sd if sd > 1e-12 else 0.0

    stable = True
    if ma20 > 0 and abs(mid / ma20 - 1.0) > spike_block_pct:
        stable = False

    regime = "unknown"
    if len(closes) >= 26:
        short = _sma(closes, 12)
        long = _sma(closes, 26)
        if short > long * 1.0005:
            regime = "trend_up"
        elif short < long * 0.9995:
            regime = "trend_down"
        else:
            regime = "range"
    elif len(closes) >= 5:
        regime = "range"

    if vol24 > 0 and vol24 < min_quote_volume_24h:
        stable = False

    return FeatureSnapshot(
        product_id=product_id,
        bid=bid,
        ask=ask,
        mid=mid,
        spread_pct=spread_pct,
        quote_volume_24h=vol24,
        stable=stable,
        regime=regime,
        ma20=ma20,
        z_score=z_score,
    )


def features_to_dict(f: FeatureSnapshot) -> Dict[str, Any]:
    return {
        "product_id": f.product_id,
        "bid": f.bid,
        "ask": f.ask,
        "mid": f.mid,
        "spread_pct": f.spread_pct,
        "quote_volume_24h": f.quote_volume_24h,
        "stable": f.stable,
        "regime": f.regime,
        "ma20": f.ma20,
        "z_score": f.z_score,
    }
