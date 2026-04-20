"""
Gate B — Momentum Scoring Engine (continuation probability, not “buy all gainers”).

Computes six 0–100 sub-scores, weighted blend, dynamic threshold, failure filters,
adaptive learning from trade outcomes, and position-size multipliers.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Default weights (spec): must sum to 1.0
DEFAULT_WEIGHTS: Tuple[float, ...] = (
    0.25,  # price_momentum
    0.25,  # volume_surge
    0.20,  # continuation_structure
    0.15,  # liquidity_score
    0.10,  # volatility_quality
    0.05,  # correlation_factor
)


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    if abs(b) < 1e-18:
        return default
    return a / b


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    n = min(len(xs), len(ys))
    if n < 3:
        return 0.0
    xs = [float(xs[i]) for i in range(n)]
    ys = [float(ys[i]) for i in range(n)]
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    if dx < 1e-18 or dy < 1e-18:
        return 0.0
    return _clamp(num / (dx * dy), -1.0, 1.0)


@dataclass
class MomentumComponentScores:
    price_momentum: float = 0.0
    volume_surge: float = 0.0
    continuation_structure: float = 0.0
    liquidity_score: float = 0.0
    volatility_quality: float = 0.0
    correlation_factor: float = 50.0

    def as_tuple(self) -> Tuple[float, ...]:
        return (
            self.price_momentum,
            self.volume_surge,
            self.continuation_structure,
            self.liquidity_score,
            self.volatility_quality,
            self.correlation_factor,
        )

    def as_dict(self) -> Dict[str, float]:
        return {
            "price_momentum": self.price_momentum,
            "volume_surge": self.volume_surge,
            "continuation_structure": self.continuation_structure,
            "liquidity_score": self.liquidity_score,
            "volatility_quality": self.volatility_quality,
            "correlation_factor": self.correlation_factor,
        }


@dataclass
class MomentumAssetSnapshot:
    """One product at scan time — all optional fields have safe defaults."""

    product_id: str
    closes: List[float] = field(default_factory=list)
    """Mid prices oldest → newest (e.g. from market_memory)."""
    volume_recent_quote: Optional[float] = None
    volume_baseline_quote: Optional[float] = None
    spread_bps: Optional[float] = None
    depth_bid_usd: Optional[float] = None
    depth_ask_usd: Optional[float] = None
    intended_trade_usd: float = 100.0
    btc_closes: List[float] = field(default_factory=list)
    """Aligned length optional; if empty, correlation_factor is neutral."""
    lookback_bars: int = 24
    short_horizon: int = 5
    long_horizon: int = 15


@dataclass
class MomentumAssetResult:
    product_id: str
    components: MomentumComponentScores
    momentum_score: float
    failure_multiplier: float
    near_peak: bool
    flags: List[str] = field(default_factory=list)
    truth_provenance: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MomentumScanResult:
    ts: float
    market_strength_0_1: float
    effective_threshold: float
    ranked: List[MomentumAssetResult]
    selected_product_ids: List[str]
    """Top K by score that pass threshold and are not near_peak."""
    weights_used: Tuple[float, ...]
    scan_notes: List[str] = field(default_factory=list)


def _log_returns(closes: List[float]) -> List[float]:
    out: List[float] = []
    for i in range(1, len(closes)):
        a, b = closes[i - 1], closes[i]
        if a > 0 and b > 0:
            out.append(math.log(b / a))
    return out


def score_price_momentum_and_accel(closes: List[float], short_h: int, long_h: int) -> float:
    if len(closes) < max(short_h, 4) + 1:
        return 25.0
    c = closes[-1]
    c_s = closes[-1 - short_h]
    c_l = closes[-1 - long_h] if len(closes) > long_h else closes[0]
    r_s = _safe_div(c - c_s, c_s, 0.0)
    r_l = _safe_div(c - c_l, c_l, 0.0)
    mom = max(0.0, r_s) * 800.0
    accel = max(0.0, r_s - _safe_div(c_s - closes[-1 - 2 * short_h], closes[-1 - 2 * short_h], 0.0)) if len(closes) > 2 * short_h + 1 else 0.0
    accel_pts = max(0.0, accel) * 400.0
    blend = min(100.0, mom * 0.65 + min(35.0, r_l * 400.0) * 0.35 + accel_pts * 0.15)
    return _clamp(blend)


def score_volume_surge(
    recent: Optional[float],
    baseline: Optional[float],
) -> float:
    if recent is None or baseline is None or baseline <= 0:
        return 50.0
    ratio = recent / max(baseline, 1e-9)
    return _clamp(100.0 * (1.0 - math.exp(-0.5 * max(0.0, ratio - 1.0))))


def score_continuation_structure(closes: List[float], lookback: int) -> float:
    if len(closes) < 5:
        return 30.0
    window = closes[-min(lookback, len(closes)) :]
    ups = 0
    for i in range(1, len(window)):
        if window[i] > window[i - 1]:
            ups += 1
    up_ratio = ups / max(1, len(window) - 1)
    hh = 0
    for i in range(1, len(window)):
        if window[i] > max(window[:i]):
            hh += 1
    hh_ratio = hh / max(1, len(window) - 1)
    spike_only = False
    if len(window) >= 5:
        rets = [abs(window[i] / window[i - 1] - 1.0) for i in range(1, len(window))]
        mx = max(rets)
        med = sorted(rets)[len(rets) // 2]
        if mx > 5 * max(med, 1e-8) and up_ratio < 0.35:
            spike_only = True
    base = 50.0 * up_ratio + 40.0 * hh_ratio
    if spike_only:
        base *= 0.35
    return _clamp(base)


def score_liquidity(
    spread_bps: Optional[float],
    depth_bid_usd: Optional[float],
    depth_ask_usd: Optional[float],
    trade_usd: float,
) -> float:
    s, _meta = score_liquidity_with_provenance(spread_bps, depth_bid_usd, depth_ask_usd, trade_usd)
    return s


def score_liquidity_with_provenance(
    spread_bps: Optional[float],
    depth_bid_usd: Optional[float],
    depth_ask_usd: Optional[float],
    trade_usd: float,
) -> Tuple[float, Dict[str, Any]]:
    """
    Liquidity sub-score 0–100. If ``spread_bps`` is missing, **do not** invent a bps measurement —
    score from depth-only + conservative floor and label provenance.
    """
    meta: Dict[str, Any] = {
        "liquidity_spread_measurement_status": "measured" if spread_bps is not None else "missing",
    }
    depth = None
    if depth_bid_usd is not None and depth_ask_usd is not None:
        depth = min(float(depth_bid_usd), float(depth_ask_usd))
    meta["liquidity_depth_measurement_status"] = "measured" if depth is not None and depth > 0 else "missing"

    if spread_bps is None:
        meta["liquidity_spread_component_note"] = (
            "No measured spread_bps in row: liquidity sub-score uses depth-only (if present) + conservative floor — "
            "not an implied spread in bps."
        )
        if depth is None or depth <= 0:
            return _clamp(26.0), meta
        cov = _clamp(100.0 * (1.0 - math.exp(-depth / max(trade_usd * 3.0, 1.0))))
        return _clamp(cov * 0.68 + 10.0), meta

    sp = float(spread_bps)
    meta["measured_spread_bps_used_in_model"] = sp
    sp_s = _clamp(100.0 * math.exp(-sp / 40.0), 0.0, 100.0)
    if depth is None or depth <= 0:
        return _clamp(sp_s * 0.85 + 10.0), meta
    cov = _clamp(100.0 * (1.0 - math.exp(-depth / max(trade_usd * 3.0, 1.0))))
    return _clamp(0.55 * sp_s + 0.45 * cov), meta


def score_volatility_quality(closes: List[float]) -> float:
    if len(closes) < 6:
        return 45.0
    lr = _log_returns(closes[-40:])
    if len(lr) < 4:
        return 45.0
    net = abs(sum(lr))
    path = sum(abs(x) for x in lr) + 1e-12
    trendiness = net / path
    m = sum(lr) / len(lr)
    var = sum((x - m) ** 2 for x in lr) / len(lr)
    chop = _safe_div(math.sqrt(max(var, 0.0)), abs(m) + 1e-8, 10.0)
    quality = 100.0 * trendiness * math.exp(-min(3.0, chop))
    return _clamp(quality)


def score_correlation_factor(asset_closes: List[float], btc_closes: List[float]) -> float:
    if len(btc_closes) < 6 or len(asset_closes) < 6:
        return 55.0
    n = min(len(asset_closes), len(btc_closes), 40)
    a = asset_closes[-n:]
    b = btc_closes[-n:]
    ar = _log_returns(a)
    br = _log_returns(b)
    m = min(len(ar), len(br))
    if m < 4:
        return 55.0
    rho = _pearson(ar[-m:], br[-m:])
    return _clamp(100.0 * (1.0 - abs(rho)))


def detect_near_peak(closes: List[float], lookback: int) -> bool:
    """
    "Near peak" for entry timing: at the top of the recent range *and* a blow-off bar
    (last move much larger than typical bars). Smooth uptrends make new highs every bar
    — that is not a peak-exhaustion signal by itself.
    """
    if len(closes) < max(lookback, 8) + 1:
        return False
    w = closes[-lookback:]
    lo, hi = min(w), max(w)
    last = closes[-1]
    if hi <= lo or last < hi * 0.999:
        return False
    rets = [abs(w[i] / w[i - 1] - 1.0) for i in range(1, len(w)) if w[i - 1] > 0]
    if len(rets) < 3:
        return False
    med = sorted(rets)[len(rets) // 2]
    last_ret = abs(w[-1] / w[-2] - 1.0) if w[-2] > 0 else 0.0
    return last_ret > 3.5 * max(med, 1e-8)


def failure_filter_multiplier(
    closes: List[float],
    components: MomentumComponentScores,
) -> Tuple[float, List[str]]:
    flags: List[str] = []
    mult = 1.0
    if len(closes) >= 6:
        lr = _log_returns(closes[-20:])
        if len(lr) >= 4:
            m = sum(lr) / len(lr)
            var = sum((x - m) ** 2 for x in lr) / len(lr)
            chaos = math.sqrt(max(var, 0.0)) / (abs(m) + 1e-8)
            if chaos > 4.0 and components.continuation_structure < 45:
                mult *= 0.35
                flags.append("chaos_volatility")
    if len(closes) >= 7:
        w = closes[-7:]
        rets = [abs(w[i] / w[i - 1] - 1.0) for i in range(1, len(w))]
        mx = max(rets)
        med = sorted(rets)[len(rets) // 2]
        if mx > 6 * max(med, 1e-9) and components.continuation_structure < 40:
            mult *= 0.45
            flags.append("spike_no_continuation")
    return mult, flags


def combine_momentum_score(
    c: MomentumComponentScores,
    weights: Sequence[float],
) -> float:
    w = list(weights)
    if len(w) != 6:
        w = list(DEFAULT_WEIGHTS)
    s = sum(w)
    if s <= 0:
        w = list(DEFAULT_WEIGHTS)
        s = sum(w)
    w = [x / s for x in w]
    t = c.as_tuple()
    raw = sum(w[i] * t[i] for i in range(6))
    return _clamp(raw)


def compute_components_for_snapshot(s: MomentumAssetSnapshot) -> MomentumComponentScores:
    closes = [float(x) for x in s.closes if float(x) > 0]
    sh = max(3, min(s.short_horizon, 30))
    lh = max(sh + 1, min(s.long_horizon, 60))
    pm = score_price_momentum_and_accel(closes, sh, lh)
    vs = score_volume_surge(s.volume_recent_quote, s.volume_baseline_quote)
    cs = score_continuation_structure(closes, max(8, min(s.lookback_bars, 80)))
    lq, _lq_meta = score_liquidity_with_provenance(
        s.spread_bps, s.depth_bid_usd, s.depth_ask_usd, s.intended_trade_usd
    )
    vq = score_volatility_quality(closes)
    cf = score_correlation_factor(closes, s.btc_closes)
    return MomentumComponentScores(
        price_momentum=pm,
        volume_surge=vs,
        continuation_structure=cs,
        liquidity_score=lq,
        volatility_quality=vq,
        correlation_factor=cf,
    )


def compute_components_for_snapshot_with_liquidity_meta(
    s: MomentumAssetSnapshot,
) -> Tuple[MomentumComponentScores, Dict[str, Any]]:
    closes = [float(x) for x in s.closes if float(x) > 0]
    sh = max(3, min(s.short_horizon, 30))
    lh = max(sh + 1, min(s.long_horizon, 60))
    pm = score_price_momentum_and_accel(closes, sh, lh)
    vs = score_volume_surge(s.volume_recent_quote, s.volume_baseline_quote)
    cs = score_continuation_structure(closes, max(8, min(s.lookback_bars, 80)))
    lq, lq_meta = score_liquidity_with_provenance(
        s.spread_bps, s.depth_bid_usd, s.depth_ask_usd, s.intended_trade_usd
    )
    vq = score_volatility_quality(closes)
    cf = score_correlation_factor(closes, s.btc_closes)
    comp = MomentumComponentScores(
        price_momentum=pm,
        volume_surge=vs,
        continuation_structure=cs,
        liquidity_score=lq,
        volatility_quality=vq,
        correlation_factor=cf,
    )
    return comp, {"liquidity_subscore": lq_meta}


def market_strength_0_1(results: List[MomentumAssetResult], top_n: int = 5) -> float:
    if not results:
        return 0.5
    scores = sorted((r.momentum_score for r in results), reverse=True)[: max(1, top_n)]
    return _clamp(sum(scores) / (100.0 * len(scores)), 0.0, 1.0)


def dynamic_threshold(
    base: float,
    market_strength_01: float,
    *,
    weak_adjust: float = 8.0,
    strong_adjust: float = 8.0,
) -> float:
    """
    Weak market (low strength) → raise threshold; strong → lower.
    market_strength_01 around 0.5 → no change.
    """
    delta = (0.5 - market_strength_01) * (weak_adjust + strong_adjust)
    return _clamp(base + delta * 0.5, 50.0, 92.0)


def position_size_multiplier(score_0_100: float, threshold: float) -> float:
    """Higher score → larger allocation; below threshold floor at small positive."""
    if score_0_100 < threshold:
        return max(0.15, (score_0_100 / max(threshold, 1.0)) ** 2 * 0.4)
    span = 100.0 - threshold + 1e-9
    return _clamp(0.5 + 0.5 * (score_0_100 - threshold) / span, 0.2, 1.0)


def _state_file_path(explicit: Optional[Path] = None) -> Path:
    if explicit is not None:
        return explicit
    from trading_ai.governance.storage_architecture import shark_state_path

    return shark_state_path("momentum_scoring_state.json")


@dataclass
class AdaptiveMomentumState:
    weights: List[float] = field(default_factory=lambda: list(DEFAULT_WEIGHTS))
    threshold: float = 70.0
    trades_recorded: int = 0
    wins: int = 0
    loss_streak: int = 0
    component_win_sum: List[float] = field(default_factory=lambda: [0.0] * 6)
    component_loss_sum: List[float] = field(default_factory=lambda: [0.0] * 6)
    win_count_c: int = 0
    loss_count_c: int = 0
    last_updated: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "weights": list(self.weights),
            "threshold": self.threshold,
            "trades_recorded": self.trades_recorded,
            "wins": self.wins,
            "loss_streak": self.loss_streak,
            "component_win_sum": list(self.component_win_sum),
            "component_loss_sum": list(self.component_loss_sum),
            "win_count_c": self.win_count_c,
            "loss_count_c": self.loss_count_c,
            "last_updated": self.last_updated,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> AdaptiveMomentumState:
        w = d.get("weights")
        if not isinstance(w, list) or len(w) != 6:
            w = list(DEFAULT_WEIGHTS)
        cws = d.get("component_win_sum")
        cls_ = d.get("component_loss_sum")
        if not isinstance(cws, list) or len(cws) != 6:
            cws = [0.0] * 6
        if not isinstance(cls_, list) or len(cls_) != 6:
            cls_ = [0.0] * 6
        return AdaptiveMomentumState(
            weights=[float(x) for x in w],
            threshold=float(d.get("threshold") or 70.0),
            trades_recorded=int(d.get("trades_recorded") or 0),
            wins=int(d.get("wins") or 0),
            loss_streak=int(d.get("loss_streak") or 0),
            component_win_sum=[float(x) for x in cws],
            component_loss_sum=[float(x) for x in cls_],
            win_count_c=int(d.get("win_count_c") or 0),
            loss_count_c=int(d.get("loss_count_c") or 0),
            last_updated=float(d.get("last_updated") or 0.0),
        )


class AdaptiveMomentumLearner:
    """Persists weights + threshold; nudges from trade outcomes."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = _state_file_path(path)
        self.state = AdaptiveMomentumState()
        self._load()

    def _load(self) -> None:
        if not self._path.is_file():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self.state = AdaptiveMomentumState.from_dict(raw)
        except Exception as exc:
            logger.warning("momentum learner load: %s", exc)

    def save(self) -> None:
        self.state.last_updated = time.time()
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self.state.to_dict(), indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("momentum learner save: %s", exc)

    def effective_weights(self) -> Tuple[float, ...]:
        w = self.state.weights
        if len(w) != 6:
            w = list(DEFAULT_WEIGHTS)
        s = sum(max(0.0, float(x)) for x in w)
        if s <= 0:
            return DEFAULT_WEIGHTS
        return tuple(max(0.0, float(x)) / s for x in w)

    def record_trade_outcome(
        self,
        *,
        momentum_score_at_entry: float,
        components: MomentumComponentScores,
        won: bool,
        learning_rate: float = 0.02,
        threshold_lr: float = 0.15,
    ) -> None:
        st = self.state
        st.trades_recorded += 1
        ct = components.as_tuple()
        if won:
            st.wins += 1
            st.loss_streak = 0
            st.win_count_c += 1
            for i in range(6):
                st.component_win_sum[i] += ct[i]
        else:
            st.loss_streak += 1
            st.loss_count_c += 1
            for i in range(6):
                st.component_loss_sum[i] += ct[i]

        ew = list(self.effective_weights())
        if st.win_count_c >= 3 and st.loss_count_c >= 3:
            aw = [st.component_win_sum[i] / max(1, st.win_count_c) for i in range(6)]
            al = [st.component_loss_sum[i] / max(1, st.loss_count_c) for i in range(6)]
            for i in range(6):
                grad = (aw[i] - al[i]) / 100.0
                ew[i] = max(0.02, ew[i] + learning_rate * grad)
        s = sum(ew)
        st.weights = [x / s for x in ew]

        if won:
            st.threshold = _clamp(st.threshold - threshold_lr * (momentum_score_at_entry / 100.0 - 0.65), 55.0, 88.0)
        else:
            st.threshold = _clamp(st.threshold + threshold_lr * 0.35 * (1.0 + min(3, st.loss_streak) * 0.1), 55.0, 90.0)

        self.save()

    def effective_threshold(self, market_strength_01: float, base_override: Optional[float] = None) -> float:
        base = float(base_override) if base_override is not None else self.state.threshold
        return dynamic_threshold(base, market_strength_01)


def run_momentum_scan(
    snapshots: Sequence[MomentumAssetSnapshot],
    *,
    learner: Optional[AdaptiveMomentumLearner] = None,
    base_threshold: Optional[float] = None,
    top_k: int = 5,
    weights: Optional[Sequence[float]] = None,
) -> MomentumScanResult:
    """
    Full scan: per-asset components → weighted score → failure filters → rank.
    Selects up to ``top_k`` product_ids that pass effective threshold and are not near_peak.
    """
    wts: Tuple[float, ...]
    if weights is not None and len(weights) == 6:
        s = sum(max(0.0, float(x)) for x in weights)
        if s > 0:
            wts = tuple(max(0.0, float(x)) / s for x in weights)
        else:
            wts = learner.effective_weights() if learner else DEFAULT_WEIGHTS
    else:
        wts = learner.effective_weights() if learner else DEFAULT_WEIGHTS

    ranked_results: List[MomentumAssetResult] = []
    for sn in snapshots:
        pid = str(sn.product_id or "").strip().upper()
        if not pid:
            continue
        comp, liq_meta = compute_components_for_snapshot_with_liquidity_meta(sn)
        raw_score = combine_momentum_score(comp, wts)
        fm, ff = failure_filter_multiplier(sn.closes, comp)
        final = _clamp(raw_score * fm)
        npk = detect_near_peak(sn.closes, max(8, min(sn.lookback_bars, 40)))
        ranked_results.append(
            MomentumAssetResult(
                product_id=pid,
                components=replace(comp),
                momentum_score=final,
                failure_multiplier=fm,
                near_peak=npk,
                flags=ff,
                truth_provenance={"liquidity": liq_meta},
            )
        )

    ranked_results.sort(key=lambda r: r.momentum_score, reverse=True)
    mstr = market_strength_0_1(ranked_results, top_n=5)
    eff_thr: float
    if learner is not None:
        eff_thr = learner.effective_threshold(mstr, base_override=base_threshold)
    else:
        eff_thr = dynamic_threshold(float(base_threshold or 70.0), mstr)

    selected: List[str] = []
    for r in ranked_results:
        if r.momentum_score <= eff_thr:
            continue
        if r.near_peak:
            continue
        selected.append(r.product_id)
        if len(selected) >= top_k:
            break

    notes: List[str] = [
        f"market_strength={mstr:.3f}",
        f"effective_threshold={eff_thr:.2f}",
        f"candidates={len(ranked_results)}",
    ]
    return MomentumScanResult(
        ts=time.time(),
        market_strength_0_1=mstr,
        effective_threshold=eff_thr,
        ranked=ranked_results,
        selected_product_ids=selected[:top_k],
        weights_used=wts,
        scan_notes=notes,
    )


def snapshot_from_row(row: Dict[str, Any]) -> Optional[MomentumAssetSnapshot]:
    """Build snapshot from a loose dict (e.g. scanner / REST merge)."""
    pid = str(row.get("product_id") or "").strip()
    if not pid:
        return None
    closes = row.get("closes") or row.get("mid_closes")
    if not isinstance(closes, list):
        closes = []
    closes_f: List[float] = []
    for x in closes:
        try:
            closes_f.append(float(x))
        except (TypeError, ValueError):
            pass
    btc = row.get("btc_closes")
    btc_f: List[float] = []
    if isinstance(btc, list):
        for x in btc:
            try:
                btc_f.append(float(x))
            except (TypeError, ValueError):
                pass
    return MomentumAssetSnapshot(
        product_id=pid,
        closes=closes_f,
        volume_recent_quote=_f_or_none(row.get("volume_recent_quote")),
        volume_baseline_quote=_f_or_none(row.get("volume_baseline_quote")),
        spread_bps=_f_or_none(row.get("spread_bps")),
        depth_bid_usd=_f_or_none(row.get("depth_bid_usd")),
        depth_ask_usd=_f_or_none(row.get("depth_ask_usd")),
        intended_trade_usd=float(row.get("intended_trade_usd") or 100.0),
        btc_closes=btc_f,
        lookback_bars=int(row.get("lookback_bars") or 24),
        short_horizon=int(row.get("short_horizon") or 5),
        long_horizon=int(row.get("long_horizon") or 15),
    )


def _f_or_none(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def env_top_k(default: int = 5) -> int:
    raw = (os.environ.get("GATE_B_MOMENTUM_TOP_K") or "").strip()
    if not raw:
        return max(3, min(12, default))
    try:
        return max(3, min(12, int(raw)))
    except ValueError:
        return max(3, min(12, default))
