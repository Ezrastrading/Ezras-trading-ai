"""Short-horizon inefficiency signals from snapshots (above Reality Lock — advisory only)."""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional


def _env_float(name: str, default: float) -> float:
    try:
        return float((os.environ.get(name) or "").strip() or default)
    except ValueError:
        return default


# Execution hook metadata (NTE / routers may raise priority; gates still apply first.)
LATENCY_TRADE_PRIORITY = "HIGH"
LATENCY_MAX_HOLD_SECONDS = 10.0

LATENCY_PRICE_THRESHOLD = _env_float("LATENCY_PRICE_THRESHOLD", 0.0008)
LATENCY_SPREAD_RATIO_MAX = _env_float("LATENCY_SPREAD_RATIO_MAX", 0.5)
LATENCY_VOLUME_RATIO_MIN = _env_float("LATENCY_VOLUME_RATIO_MIN", 2.0)


@dataclass
class LatencySignal:
    product_id: str
    venue: str
    signal_type: str  # "price_jump" | "spread_collapse" | "volume_spike"
    strength: float
    timestamp: float


def _f(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def detect_latency_signal(market_snapshot: Mapping[str, Any]) -> List[LatencySignal]:
    """
    Detect short-lived microstructure signals from a numeric snapshot.

    Expected keys (best-effort; missing values skip that branch):
    - price_now, price_1s_ago — absolute mid change gate
    - spread_now, spread_avg — spread collapse vs rolling average
    - volume_1s, volume_avg — volume spike vs baseline
    """
    signals: List[LatencySignal] = []
    pid = str(market_snapshot.get("product_id") or "unknown")
    venue = str(market_snapshot.get("venue") or market_snapshot.get("avenue") or "unknown")
    ts = _f(market_snapshot.get("timestamp"), time.time())

    pn = _f(market_snapshot.get("price_now"))
    p1 = _f(market_snapshot.get("price_1s_ago"))
    if pn > 0 and p1 > 0:
        if abs(pn - p1) / max(pn, 1e-12) > LATENCY_PRICE_THRESHOLD:
            strength = min(1.0, abs(pn - p1) / max(pn, 1e-12) / max(LATENCY_PRICE_THRESHOLD, 1e-12))
            signals.append(
                LatencySignal(
                    product_id=pid,
                    venue=venue,
                    signal_type="price_jump",
                    strength=strength,
                    timestamp=ts,
                )
            )

    spread_now = _f(market_snapshot.get("spread_now"))
    spread_avg = _f(market_snapshot.get("spread_avg"))
    if spread_now > 0 and spread_avg > 0 and spread_now < spread_avg * LATENCY_SPREAD_RATIO_MAX:
        strength = min(1.0, 1.0 - (spread_now / max(spread_avg, 1e-12)))
        signals.append(
            LatencySignal(
                product_id=pid,
                venue=venue,
                signal_type="spread_collapse",
                strength=strength,
                timestamp=ts,
            )
        )

    v1 = _f(market_snapshot.get("volume_1s"))
    va = _f(market_snapshot.get("volume_avg"))
    if v1 > 0 and va > 0 and v1 > va * LATENCY_VOLUME_RATIO_MIN:
        strength = min(1.0, v1 / max(va * LATENCY_VOLUME_RATIO_MIN, 1e-12))
        signals.append(
            LatencySignal(
                product_id=pid,
                venue=venue,
                signal_type="volume_spike",
                strength=strength,
                timestamp=ts,
            )
        )

    return signals


def build_market_snapshot_for_latency(
    *,
    product_id: str,
    venue: str,
    mid: float,
    spread_pct: float,
    store: Any,
    quote_volume_24h: float,
) -> Dict[str, Any]:
    """
    Build a snapshot dict from NTE MemoryStore history (closes + optional volume proxy).

    Uses last two mids for ~1s proxy when timestamps are not stored per tick.
    """
    price_now = float(mid)
    price_1s_ago = price_now
    spread_now = float(spread_pct)
    spread_avg = spread_now
    volume_1s = 0.0
    volume_avg = 1.0

    try:
        mm = store.load_json("market_memory.json")
        closes = (mm.get("closes") or {}).get(product_id)
        if isinstance(closes, list) and len(closes) >= 2:
            price_1s_ago = float(closes[-2])
            # rough spread average from recent mids dispersion
            tail = [float(x) for x in closes[-20:] if isinstance(x, (int, float))]
            if len(tail) >= 3:
                m = sum(tail) / len(tail)
                dev = math.sqrt(sum((x - m) ** 2 for x in tail) / len(tail))
                spread_avg = max(spread_now, min(0.05, (dev / max(m, 1e-12)) * 2.0))
        vol_hist = (mm.get("volume_1s_samples") or {}).get(product_id)
        if isinstance(vol_hist, list) and vol_hist:
            volume_1s = float(vol_hist[-1]) if vol_hist else 0.0
            recent = [float(x) for x in vol_hist[-30:] if isinstance(x, (int, float))]
            if recent:
                volume_avg = max(1e-12, sum(recent) / len(recent))
    except Exception:
        pass

    # 24h volume proxy: treat as average scale when intraday samples missing
    if volume_1s <= 0 and quote_volume_24h > 0:
        volume_avg = max(volume_avg, quote_volume_24h / 86400.0)
        volume_1s = volume_avg

    return {
        "product_id": product_id,
        "venue": venue,
        "timestamp": time.time(),
        "price_now": price_now,
        "price_1s_ago": price_1s_ago,
        "spread_now": spread_now,
        "spread_avg": spread_avg,
        "volume_1s": volume_1s,
        "volume_avg": volume_avg,
    }


def record_volume_sample(store: Any, product_id: str, volume_1s: float, max_len: int = 120) -> None:
    """Optional: persist 1s volume samples for volume_spike detection."""
    try:
        mm = store.load_json("market_memory.json")
        vs = mm.get("volume_1s_samples") or {}
        if not isinstance(vs, dict):
            vs = {}
        arr = vs.get(product_id)
        if not isinstance(arr, list):
            arr = []
        arr.append(float(volume_1s))
        arr = arr[-max_len:]
        vs[product_id] = arr
        mm["volume_1s_samples"] = vs
        store.save_json("market_memory.json", mm)
    except Exception:
        pass
