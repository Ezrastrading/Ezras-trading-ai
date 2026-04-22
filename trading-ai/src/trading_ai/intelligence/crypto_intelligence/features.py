"""Deterministic market-structure features from compact price sequences.

Non-negotiable honesty:
- If required inputs are missing (e.g. candle closes), we return explicit missing notes.
- We do not fabricate candle structure from a single tick.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple


def _f(x: Any, d: float = 0.0) -> float:
    try:
        return float(x) if x is not None else d
    except (TypeError, ValueError):
        return d


def _safe_div(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return a / b


def _mean(xs: List[float]) -> float:
    return sum(xs) / max(1, len(xs))


def _stdev(xs: List[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    v = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(max(0.0, v))


def _returns(closes: List[float]) -> List[float]:
    out: List[float] = []
    for i in range(1, len(closes)):
        a = closes[i - 1]
        b = closes[i]
        if a <= 0 or b <= 0:
            continue
        out.append((b / a) - 1.0)
    return out


def _sma(xs: List[float], n: int) -> Optional[float]:
    if n <= 0 or len(xs) < n:
        return None
    return _mean(xs[-n:])


def _minmax(xs: List[float]) -> Tuple[Optional[float], Optional[float]]:
    if not xs:
        return None, None
    return min(xs), max(xs)


@dataclass(frozen=True)
class StructureFeatures:
    # Market identifiers
    product_id: str
    venue: str
    gate_id: str
    timestamp_unix: Optional[float]

    # Core derived features (normalized)
    n_closes: int
    last_close: Optional[float]
    ret_1: Optional[float]
    ret_n: Optional[float]
    vol_ret_stdev: Optional[float]
    range_pct: Optional[float]
    compression_score_0_1: Optional[float]
    trend_slope_norm: Optional[float]
    extension_vs_sma_norm: Optional[float]
    dist_to_range_high_pct: Optional[float]
    dist_to_range_low_pct: Optional[float]

    # Inputs if present
    spread_bps: Optional[float]
    volume_24h_usd: Optional[float]
    book_depth_usd: Optional[float]
    move_pct: Optional[float]
    volume_surge_ratio: Optional[float]
    continuation_candles: Optional[int]
    velocity_score: Optional[float]
    candle_structure_score: Optional[float]
    exhaustion_risk: Optional[float]

    # Classification hooks
    setup_appearance: str
    setup_family: str
    missing_notes: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "product_id": self.product_id,
            "venue": self.venue,
            "gate_id": self.gate_id,
            "timestamp_unix": self.timestamp_unix,
            "n_closes": self.n_closes,
            "last_close": self.last_close,
            "ret_1": self.ret_1,
            "ret_n": self.ret_n,
            "vol_ret_stdev": self.vol_ret_stdev,
            "range_pct": self.range_pct,
            "compression_score_0_1": self.compression_score_0_1,
            "trend_slope_norm": self.trend_slope_norm,
            "extension_vs_sma_norm": self.extension_vs_sma_norm,
            "dist_to_range_high_pct": self.dist_to_range_high_pct,
            "dist_to_range_low_pct": self.dist_to_range_low_pct,
            "spread_bps": self.spread_bps,
            "volume_24h_usd": self.volume_24h_usd,
            "book_depth_usd": self.book_depth_usd,
            "move_pct": self.move_pct,
            "volume_surge_ratio": self.volume_surge_ratio,
            "continuation_candles": self.continuation_candles,
            "velocity_score": self.velocity_score,
            "candle_structure_score": self.candle_structure_score,
            "exhaustion_risk": self.exhaustion_risk,
            "setup_appearance": self.setup_appearance,
            "setup_family": self.setup_family,
            "missing_notes": list(self.missing_notes),
        }


def _classify_setup_appearance(*, f: StructureFeatures) -> str:
    # BTC-first heuristics (works for others too).
    exh = float(f.exhaustion_risk or 0.0)
    comp = float(f.compression_score_0_1 or 0.0)
    move = float(f.move_pct or 0.0)
    cont = int(f.continuation_candles or 0)
    trend = float(f.trend_slope_norm or 0.0)
    if exh >= 0.7 and move >= 0.06:
        return "exhaustion_spike"
    if comp >= 0.65 and move >= 0.05 and cont >= 2:
        return "breakout_continuation"
    if trend > 0.002 and move < 0.03:
        return "pullback_continuation"
    if comp >= 0.65 and move < 0.03:
        return "compression_no_break"
    if abs(trend) < 0.001 and (f.range_pct or 0.0) < 0.02:
        return "chop_low_quality"
    return "unclear_mixed"


def _setup_family(*, product_id: str, appearance: str, gate_id: str) -> str:
    pid = (product_id or "").strip().upper()
    base = "btc" if pid.startswith("BTC") else ("eth" if pid.startswith("ETH") else "alt")
    return f"{gate_id.lower()}::{base}::{appearance}"


def extract_structure_features(
    row: Mapping[str, Any],
    *,
    product_id: str,
    venue: str,
    gate_id: str,
    timestamp_unix: Optional[float] = None,
    closes_key: str = "closes",
) -> StructureFeatures:
    closes_raw = row.get(closes_key)
    missing: List[str] = []
    closes: List[float] = []
    if isinstance(closes_raw, list):
        for x in closes_raw[-240:]:
            v = _f(x, d=float("nan"))
            if math.isfinite(v) and v > 0:
                closes.append(float(v))
    if len(closes) < 10:
        missing.append("missing_or_thin_closes")

    last = closes[-1] if closes else None
    rets = _returns(closes) if len(closes) >= 2 else []
    ret_1 = rets[-1] if rets else None
    ret_n = (closes[-1] / closes[0] - 1.0) if len(closes) >= 2 and closes[0] > 0 else None
    vol = _stdev(rets) if rets else None
    lo, hi = _minmax(closes)
    range_pct = ((hi - lo) / lo) if lo and hi and lo > 0 else None
    # Compression: low range vs volatility proxy (bounded 0..1).
    comp = None
    if range_pct is not None and vol is not None:
        # If range is small relative to vol, treat as compressed.
        denom = max(1e-9, abs(vol) * math.sqrt(max(1.0, float(len(rets)))))
        raw = 1.0 - min(1.0, max(0.0, range_pct / max(1e-9, denom)))
        comp = float(min(1.0, max(0.0, raw)))
    # Trend slope: regression on closes (normalized by last price).
    trend = None
    if len(closes) >= 10 and last and last > 0:
        n = len(closes)
        xs = list(range(n))
        x_m = (n - 1) / 2.0
        y_m = _mean(closes)
        num = sum((x - x_m) * (y - y_m) for x, y in zip(xs, closes))
        den = sum((x - x_m) ** 2 for x in xs) or 1.0
        slope = num / den
        trend = slope / last
    sma20 = _sma(closes, 20)
    ext = ((last - sma20) / sma20) if sma20 and last and sma20 > 0 else None
    d_hi = ((hi - last) / hi) if hi and last and hi > 0 else None
    d_lo = ((last - lo) / lo) if lo and last and lo > 0 else None

    f = StructureFeatures(
        product_id=str(product_id or "").strip().upper(),
        venue=str(venue or "").strip().lower() or "unknown",
        gate_id=str(gate_id or "").strip().lower() or "unknown",
        timestamp_unix=timestamp_unix,
        n_closes=len(closes),
        last_close=last,
        ret_1=ret_1,
        ret_n=ret_n,
        vol_ret_stdev=vol,
        range_pct=range_pct,
        compression_score_0_1=comp,
        trend_slope_norm=trend,
        extension_vs_sma_norm=ext,
        dist_to_range_high_pct=d_hi,
        dist_to_range_low_pct=d_lo,
        spread_bps=_f(row.get("spread_bps"), d=float("nan")) if row.get("spread_bps") is not None else None,
        volume_24h_usd=_f(row.get("volume_24h_usd"), d=float("nan")) if row.get("volume_24h_usd") is not None else None,
        book_depth_usd=_f(row.get("book_depth_usd"), d=float("nan")) if row.get("book_depth_usd") is not None else None,
        move_pct=_f(row.get("move_pct"), d=float("nan")) if row.get("move_pct") is not None else None,
        volume_surge_ratio=_f(row.get("volume_surge_ratio"), d=float("nan")) if row.get("volume_surge_ratio") is not None else None,
        continuation_candles=int(row.get("continuation_candles")) if row.get("continuation_candles") is not None else None,
        velocity_score=_f(row.get("velocity_score"), d=float("nan")) if row.get("velocity_score") is not None else None,
        candle_structure_score=_f(row.get("candle_structure_score"), d=float("nan"))
        if row.get("candle_structure_score") is not None
        else None,
        exhaustion_risk=_f(row.get("exhaustion_risk"), d=float("nan")) if row.get("exhaustion_risk") is not None else None,
        setup_appearance="unclear_mixed",
        setup_family="",
        missing_notes=missing,
    )
    appearance = _classify_setup_appearance(f=f)
    fam = _setup_family(product_id=f.product_id, appearance=appearance, gate_id=f.gate_id)
    return StructureFeatures(**{**f.__dict__, "setup_appearance": appearance, "setup_family": fam})

