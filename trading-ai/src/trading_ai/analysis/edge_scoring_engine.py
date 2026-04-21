"""
Self-learning edge scoring engine (evidence-first).

Non-negotiable honesty:
- Scores are computed ONLY from realized trade outcomes in the Trade Intelligence databank events.
- Fee-aware: net_pnl is primary; fees/slippage are explicit penalties.
- No "green" claims: artifacts include the input window + evidence basis.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.storage.storage_adapter import LocalStorageAdapter


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _s(x: Any) -> str:
    return str(x or "").strip()


def _num(x: Any, d: float = 0.0) -> float:
    try:
        return float(x) if x is not None else d
    except (TypeError, ValueError):
        return d


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    if not math.isfinite(x):
        return lo
    return max(lo, min(hi, float(x)))


def _safe_div(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return a / b


def _parse_ts(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _timeout_exit(ev: Mapping[str, Any]) -> bool:
    r = _s(ev.get("exit_reason") or ev.get("close_reason") or ev.get("reason_closed") or "").lower()
    if not r:
        return False
    return ("timeout" in r) or (r in ("max_hold_timeout", "max_hold", "hold_timeout"))


def _infer_gate_id(ev: Mapping[str, Any]) -> str:
    g = _s(ev.get("gate_id") or ev.get("trading_gate") or ev.get("selected_gate") or "")
    return g.lower() if g else "unknown"


def _infer_lane_type(ev: Mapping[str, Any]) -> str:
    """
    production / experimental:
    - prefer explicit `lane` or `strategy_mode` when present
    - fall back to "production" for known production paths, else "experimental" if hinted
    """
    lane = _s(ev.get("lane") or "").lower()
    if lane in ("production", "experimental"):
        return lane
    sm = _s(ev.get("strategy_mode") or "").lower()
    if sm in ("production", "experimental"):
        return sm
    intent = _s(ev.get("execution_intent") or "").lower()
    if intent in ("profit_mode", "live"):
        return "production"
    sid = _s(ev.get("strategy_id") or "")
    if sid.startswith("EXP_") or sid.startswith("experimental_"):
        return "experimental"
    return "production"


def _infer_edge_family(ev: Mapping[str, Any]) -> str:
    # If upstream recorded an explicit edge_family, prefer it.
    ef = _s(ev.get("edge_family") or ev.get("edge_type") or "")
    if ef:
        return ef
    # Default: strategy_id is treated as the edge family in Avenue A gate edges.
    sid = _s(ev.get("strategy_id") or "")
    return sid or "unknown"


def _infer_symbol(ev: Mapping[str, Any]) -> str:
    return _s(ev.get("symbol_or_product_id") or ev.get("asset") or ev.get("product_id") or ev.get("market_id") or "")


def _infer_notional_usd(ev: Mapping[str, Any]) -> float:
    """
    Best-effort capital deployed per trade in USD.
    Uses explicit fields when present (preferred), else returns 0.0.
    """
    for k in ("notional_usd", "trade_size_usd", "size_usd", "capital_allocated", "size_dollars", "quote_usd"):
        v = ev.get(k)
        if v is None:
            continue
        try:
            n = abs(float(v))
            if n > 0:
                return n
        except (TypeError, ValueError):
            continue
    # Coinbase profit-mode raw merges use quote legs sometimes
    for k in ("quote_qty_buy", "quote_qty_sell", "buy_quote_spent", "sell_quote_received"):
        v = ev.get(k)
        if v is None:
            continue
        try:
            n = abs(float(v))
            if n > 0:
                return n
        except (TypeError, ValueError):
            continue
    return 0.0


@dataclass(frozen=True)
class EdgeScoreKey:
    avenue_id: str
    gate_id: str
    strategy_id: str
    edge_family: str
    symbol_or_product_id: str
    lane_type: str
    regime_bucket: str = ""
    time_segment: str = ""

    def to_id(self) -> str:
        # Stable identifier: used in artifacts and lookup for live bias.
        base = "|".join(
            [
                _s(self.avenue_id),
                _s(self.gate_id),
                _s(self.strategy_id),
                _s(self.edge_family),
                _s(self.symbol_or_product_id),
                _s(self.lane_type),
                _s(self.regime_bucket),
                _s(self.time_segment),
            ]
        )
        return base


@dataclass
class EdgeScore:
    # identity
    avenue_id: str
    gate_id: str
    strategy_id: str
    edge_family: str
    symbol_or_product_id: str
    lane_type: str
    # performance
    total_trades: int
    wins: int
    losses: int
    gross_pnl: float
    fees_paid: float
    net_pnl: float
    avg_net_pnl: float
    expectancy: float
    avg_win: float
    avg_loss: float
    reward_risk: float
    max_drawdown: float
    timeout_ratio: float
    fee_dominance_ratio: float
    slippage_penalty_if_available: float
    recent_5_trade_net_pnl: float
    recent_10_trade_net_pnl: float
    rolling_confidence_score: float
    rolling_stability_score: float
    edge_health_score: float
    promotion_status: str
    demotion_status: str
    last_updated_at: str
    # optional dimensions for level scoring
    regime_bucket: str = ""
    time_segment: str = ""
    evidence_window: Dict[str, Any] = None  # type: ignore[assignment]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if d.get("evidence_window") is None:
            d["evidence_window"] = {}
        return d


def _time_segment_from_close_ts(ts: Optional[datetime]) -> str:
    if ts is None:
        return ""
    h = int(ts.hour)
    if 0 <= h < 6:
        return "overnight_utc"
    if 6 <= h < 12:
        return "am_utc"
    if 12 <= h < 18:
        return "pm_utc"
    return "evening_utc"


def _recency_weights(events: List[Mapping[str, Any]], *, half_life_days: float = 14.0) -> List[float]:
    """
    Exponential recency decay by close timestamp.
    Missing timestamps get weight=0.5 (conservative).
    """
    if not events:
        return []
    now = datetime.now(timezone.utc)
    lam = math.log(2.0) / max(1e-9, float(half_life_days))
    w: List[float] = []
    for ev in events:
        ts = _parse_ts(ev.get("timestamp_close") or ev.get("timestamp") or ev.get("timestamp_close_utc"))
        if ts is None:
            w.append(0.5)
            continue
        age_days = max(0.0, (now - ts).total_seconds() / 86400.0)
        w.append(float(math.exp(-lam * age_days)))
    return w


def _weighted_mean(xs: List[float], ws: List[float]) -> float:
    if not xs or not ws or len(xs) != len(ws):
        return 0.0
    den = sum(ws)
    if den <= 0:
        return 0.0
    return sum(xs[i] * ws[i] for i in range(len(xs))) / den


def _weighted_variance(xs: List[float], ws: List[float]) -> float:
    if not xs or not ws or len(xs) != len(ws):
        return 0.0
    mu = _weighted_mean(xs, ws)
    den = sum(ws)
    if den <= 0:
        return 0.0
    return sum(ws[i] * (xs[i] - mu) ** 2 for i in range(len(xs))) / den


def _max_drawdown(pnls: List[float]) -> float:
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        cum += float(p)
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    return float(max_dd)


def _cap_outlier(pnl: float, *, cap_mult: float, scale: float) -> float:
    """
    Anti-overfit: cap single-trade contribution to +/- cap_mult * scale,
    where `scale` is based on recent typical absolute pnl.
    """
    if scale <= 0 or not math.isfinite(scale):
        return float(pnl)
    cap = abs(float(cap_mult) * float(scale))
    return max(-cap, min(cap, float(pnl)))


def _compute_component_scores(
    *,
    pnls: List[float],
    gross: List[float],
    fees: List[float],
    slips: List[float],
    timeouts: List[bool],
    notionals: List[float],
    recency_w: List[float],
) -> Dict[str, float]:
    n = len(pnls)
    if n == 0:
        return {
            "profit": 0.0,
            "stability": 0.0,
            "execution": 0.0,
            "capital_efficiency": 0.0,
            "recency": 0.0,
        }

    # Anti-overfit: cap lucky outliers for scoring (does not change reported net_pnl).
    abs_p = [abs(x) for x in pnls if math.isfinite(x)]
    scale = statistics.median(abs_p) if abs_p else 0.0
    capped = [_cap_outlier(p, cap_mult=6.0, scale=max(1e-9, scale)) for p in pnls]

    mean_net = sum(pnls) / n
    w_mean_net = _weighted_mean(capped, recency_w) if recency_w else mean_net
    var_net = statistics.pvariance(pnls) if n > 1 else 0.0
    w_var = _weighted_variance(capped, recency_w) if recency_w else var_net
    std = math.sqrt(max(0.0, var_net))
    w_std = math.sqrt(max(0.0, w_var))

    dd = _max_drawdown(pnls)
    # Profit score: prioritize fee-aware expectancy (mean net) and weighted mean net.
    profit = 50.0
    # map mean net to 0..100 softly; scale by std+1 so we don't over-reward volatile pnl
    profit = 50.0 + 35.0 * (w_mean_net / (1.0 + w_std))
    profit = _clamp(profit)

    # Stability score: lower drawdown + lower volatility.
    stability = 100.0
    stability -= min(60.0, dd * 6.0)  # dd is USD; conservative clamp
    stability -= min(35.0, w_std * 4.0)
    stability = _clamp(stability)

    # Execution quality: timeout ratio, fee drag, slippage.
    timeout_ratio = sum(1 for t in timeouts if t) / n
    fee_sum = sum(abs(f) for f in fees)
    gross_sum = sum(abs(g) for g in gross)
    fee_dom = _safe_div(fee_sum, max(1e-9, gross_sum))
    slip_avg = sum(abs(s) for s in slips) / n
    execution = 100.0
    execution -= min(55.0, timeout_ratio * 100.0 * 0.75)
    execution -= min(35.0, fee_dom * 100.0 * 0.40)
    execution -= min(25.0, slip_avg * 0.45)  # bps → points
    execution = _clamp(execution)

    # Capital efficiency: return per dollar deployed, weighted by recency.
    notional_sum = sum(max(0.0, x) for x in notionals)
    roi = _safe_div(sum(pnls), max(1e-9, notional_sum))
    w_roi = 0.0
    if recency_w and sum(recency_w) > 0:
        w_roi = _safe_div(sum(pnls[i] * recency_w[i] for i in range(n)), max(1e-9, sum(notionals[i] * recency_w[i] for i in range(n))))
    else:
        w_roi = roi
    capital_eff = 50.0 + 45.0 * (w_roi * 100.0)  # 1% ROI → +45
    capital_eff = _clamp(capital_eff)

    # Recency score: how much we trust "recent" signal vs all-time (requires enough trades).
    if n < 5:
        rec = 30.0
    else:
        last5 = sum(pnls[-5:])
        allsum = sum(pnls)
        rec = 50.0 + 25.0 * math.tanh(_safe_div(last5, max(1e-9, abs(allsum) + 1.0)))
        rec = _clamp(rec)

    return {
        "profit": float(profit),
        "stability": float(stability),
        "execution": float(execution),
        "capital_efficiency": float(capital_eff),
        "recency": float(rec),
    }


def compute_edge_score_from_events(
    events: List[Mapping[str, Any]],
    *,
    key: EdgeScoreKey,
    promotion_status: str = "experimental",
    demotion_status: str = "",
) -> EdgeScore:
    rows = [e for e in events if isinstance(e, dict)]
    pnls = [_num(e.get("net_pnl")) for e in rows]
    gross = [_num(e.get("gross_pnl")) for e in rows]
    fees = [_num(e.get("fees_paid")) for e in rows]
    slips = [abs(_num(e.get("entry_slippage_bps"))) + abs(_num(e.get("exit_slippage_bps"))) for e in rows]
    timeouts = [_timeout_exit(e) for e in rows]
    notionals = [_infer_notional_usd(e) for e in rows]
    recency_w = _recency_weights(rows, half_life_days=14.0)

    n = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    pos = [p for p in pnls if p > 0]
    neg = [p for p in pnls if p < 0]
    avg_win = sum(pos) / len(pos) if pos else 0.0
    avg_loss = sum(neg) / len(neg) if neg else 0.0  # negative

    net_sum = sum(pnls)
    gross_sum = sum(gross)
    fees_sum = sum(fees)
    avg_net = net_sum / n if n else 0.0
    expectancy = avg_net  # fee-aware empirical expectancy per trade
    rr = _safe_div(avg_win, abs(avg_loss)) if avg_loss != 0 else (float("inf") if avg_win > 0 else 0.0)
    max_dd = _max_drawdown(pnls)
    timeout_ratio = sum(1 for t in timeouts if t) / n if n else 0.0
    fee_dom = _safe_div(sum(abs(f) for f in fees), max(1e-9, sum(abs(g) for g in gross))) if n else 0.0
    slip_pen = sum(slips) / n if n else 0.0
    recent5 = sum(pnls[-5:]) if n else 0.0
    recent10 = sum(pnls[-10:]) if n else 0.0

    comps = _compute_component_scores(
        pnls=pnls,
        gross=gross,
        fees=fees,
        slips=slips,
        timeouts=timeouts,
        notionals=notionals,
        recency_w=recency_w,
    )

    # Confidence: sample size + consistency (bounded).
    sample = _clamp(100.0 * (1.0 - math.exp(-n / 35.0)))
    recent_signal = _clamp(50.0 + 25.0 * math.tanh(recent10 / max(1e-9, abs(net_sum) + 1.0))) if n else 0.0
    confidence = _clamp(sample * 0.65 + recent_signal * 0.35)

    stability_score = _clamp(comps["stability"])

    # Edge health: recent performance matters more; net>win rate; penalize fee drag + timeouts implicitly via execution.
    health = (
        comps["profit"] * 0.34
        + comps["stability"] * 0.22
        + comps["execution"] * 0.20
        + comps["capital_efficiency"] * 0.14
        + comps["recency"] * 0.10
    )
    health = _clamp(health)

    return EdgeScore(
        avenue_id=key.avenue_id,
        gate_id=key.gate_id,
        strategy_id=key.strategy_id,
        edge_family=key.edge_family,
        symbol_or_product_id=key.symbol_or_product_id,
        lane_type=key.lane_type,
        total_trades=int(n),
        wins=int(wins),
        losses=int(losses),
        gross_pnl=float(gross_sum),
        fees_paid=float(fees_sum),
        net_pnl=float(net_sum),
        avg_net_pnl=float(avg_net),
        expectancy=float(expectancy),
        avg_win=float(avg_win),
        avg_loss=float(avg_loss),
        reward_risk=float(rr if math.isfinite(rr) else 0.0),
        max_drawdown=float(max_dd),
        timeout_ratio=float(timeout_ratio),
        fee_dominance_ratio=float(fee_dom),
        slippage_penalty_if_available=float(slip_pen),
        recent_5_trade_net_pnl=float(recent5),
        recent_10_trade_net_pnl=float(recent10),
        rolling_confidence_score=float(confidence),
        rolling_stability_score=float(stability_score),
        edge_health_score=float(health),
        promotion_status=str(promotion_status),
        demotion_status=str(demotion_status),
        last_updated_at=_iso_now(),
        regime_bucket=str(key.regime_bucket or ""),
        time_segment=str(key.time_segment or ""),
        evidence_window={
            "half_life_days": 14.0,
            "outlier_cap_mult": 6.0,
            "notes": "Scores are computed from realized net_pnl (after fees) and bounded execution penalties.",
        },
    )


def build_edge_keys_for_event(ev: Mapping[str, Any]) -> List[Tuple[str, EdgeScoreKey]]:
    """
    Produce the multi-level aggregation keys required by the mission.
    Returns (level, key) pairs where `level` names the aggregation grain.
    """
    avenue_id = _s(ev.get("avenue_id") or "")
    gate_id = _infer_gate_id(ev)
    strategy_id = _s(ev.get("strategy_id") or "unknown")
    edge_family = _infer_edge_family(ev)
    symbol = _infer_symbol(ev)
    lane = _infer_lane_type(ev)
    regime_bucket = _s(ev.get("regime_bucket") or "")
    ts = _parse_ts(ev.get("timestamp_close"))
    seg = _time_segment_from_close_ts(ts)

    keys: List[Tuple[str, EdgeScoreKey]] = []

    # A. strategy level
    keys.append(
        (
            "strategy",
            EdgeScoreKey(avenue_id=avenue_id, gate_id=gate_id, strategy_id=strategy_id, edge_family=edge_family, symbol_or_product_id="ALL", lane_type=lane),
        )
    )
    # B. edge family level
    keys.append(
        (
            "edge_family",
            EdgeScoreKey(avenue_id=avenue_id, gate_id=gate_id, strategy_id="ALL", edge_family=edge_family, symbol_or_product_id="ALL", lane_type=lane),
        )
    )
    # C. gate level
    keys.append(
        (
            "gate",
            EdgeScoreKey(avenue_id=avenue_id, gate_id=gate_id, strategy_id="ALL", edge_family="ALL", symbol_or_product_id="ALL", lane_type=lane),
        )
    )
    # D. symbol/product level
    if symbol:
        keys.append(
            (
                "symbol",
                EdgeScoreKey(avenue_id=avenue_id, gate_id="ALL", strategy_id="ALL", edge_family="ALL", symbol_or_product_id=symbol, lane_type=lane),
            )
        )
    # E. strategy+symbol combo
    if symbol:
        keys.append(
            (
                "strategy_symbol",
                EdgeScoreKey(avenue_id=avenue_id, gate_id=gate_id, strategy_id=strategy_id, edge_family=edge_family, symbol_or_product_id=symbol, lane_type=lane),
            )
        )
    # F. gate+regime combo
    if regime_bucket:
        keys.append(
            (
                "gate_regime",
                EdgeScoreKey(
                    avenue_id=avenue_id,
                    gate_id=gate_id,
                    strategy_id="ALL",
                    edge_family="ALL",
                    symbol_or_product_id="ALL",
                    lane_type=lane,
                    regime_bucket=regime_bucket,
                ),
            )
        )
    # G. time-of-day segment
    if seg:
        keys.append(
            (
                "time_segment",
                EdgeScoreKey(
                    avenue_id=avenue_id,
                    gate_id=gate_id,
                    strategy_id="ALL",
                    edge_family="ALL",
                    symbol_or_product_id="ALL",
                    lane_type=lane,
                    time_segment=seg,
                ),
            )
        )

    return keys


def compute_edge_scores(
    events: Iterable[Mapping[str, Any]],
    *,
    promotion_status_by_key_id: Optional[Mapping[str, str]] = None,
    demotion_status_by_key_id: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    rows = [e for e in events if isinstance(e, Mapping)]
    buckets: Dict[Tuple[str, str], List[Mapping[str, Any]]] = {}
    keys_meta: Dict[Tuple[str, str], EdgeScoreKey] = {}

    for ev in rows:
        for level, key in build_edge_keys_for_event(ev):
            kid = key.to_id()
            k = (level, kid)
            buckets.setdefault(k, []).append(ev)
            keys_meta[k] = key

    promo = promotion_status_by_key_id or {}
    demo = demotion_status_by_key_id or {}

    scores: Dict[str, Any] = {"updated_at": _iso_now(), "levels": {}, "by_key_id": {}}

    for (level, kid), evs in buckets.items():
        key = keys_meta[(level, kid)]
        sc = compute_edge_score_from_events(
            list(evs),
            key=key,
            promotion_status=str(promo.get(kid) or ""),
            demotion_status=str(demo.get(kid) or ""),
        )
        scores["levels"].setdefault(level, [])
        scores["levels"][level].append(sc.to_dict())
        scores["by_key_id"][kid] = sc.to_dict()

    # Rankings (truth): computed per level using edge_health_score primarily.
    rankings: Dict[str, List[Dict[str, Any]]] = {}
    for level, rows2 in (scores.get("levels") or {}).items():
        if not isinstance(rows2, list):
            continue
        ranked = sorted(
            [r for r in rows2 if isinstance(r, dict)],
            key=lambda r: (float(r.get("edge_health_score") or 0.0), float(r.get("rolling_confidence_score") or 0.0)),
            reverse=True,
        )
        rankings[level] = ranked

    scores["rankings"] = rankings
    scores["honesty"] = "All scores computed from databank trade_events.jsonl (realized outcomes). No external inference."
    return scores


def write_edge_score_artifacts(
    *,
    runtime_root: Optional[Path],
    scores_payload: Dict[str, Any],
) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ad = LocalStorageAdapter(runtime_root=root)
    scores_rel = "data/control/edge_scores_truth.json"
    rankings_rel = "data/control/edge_rankings_truth.json"
    ad.write_json(scores_rel, scores_payload)
    ad.write_json(rankings_rel, {"updated_at": scores_payload.get("updated_at"), "rankings": scores_payload.get("rankings"), "honesty": scores_payload.get("honesty")})
    # Gate + symbol convenience mirrors
    gate_rank = (scores_payload.get("rankings") or {}).get("gate") if isinstance(scores_payload.get("rankings"), dict) else None
    symbol_rank = (scores_payload.get("rankings") or {}).get("symbol") if isinstance(scores_payload.get("rankings"), dict) else None
    if gate_rank is not None:
        ad.write_json("data/control/gate_rankings_truth.json", {"updated_at": scores_payload.get("updated_at"), "rows": gate_rank})
    if symbol_rank is not None:
        ad.write_json("data/control/symbol_rankings_truth.json", {"updated_at": scores_payload.get("updated_at"), "rows": symbol_rank})
    return {
        "ok": True,
        "runtime_root": str(root),
        "edge_scores_truth": str(root / scores_rel),
        "edge_rankings_truth": str(root / rankings_rel),
    }


def load_edge_rankings_truth(runtime_root: Path) -> Dict[str, Any]:
    ad = LocalStorageAdapter(runtime_root=Path(runtime_root).resolve())
    data = ad.read_json("data/control/edge_rankings_truth.json")
    return data if isinstance(data, dict) else {}


def edge_bias_multiplier_from_truth(
    *,
    runtime_root: Path,
    avenue_id: str,
    gate_id: str,
    strategy_id: str,
    edge_family: str,
    symbol_or_product_id: str,
    lane_type: str,
) -> Dict[str, Any]:
    """
    Live decision helper: convert edge health into a bounded multiplier.
    Returns dict with `multiplier`, `edge_health`, `confidence`, and `status`.
    """
    root = Path(runtime_root).resolve()
    ad = LocalStorageAdapter(runtime_root=root)
    data = ad.read_json("data/control/edge_scores_truth.json")
    if not isinstance(data, dict):
        return {"ok": False, "multiplier": 1.0, "reason": "missing_edge_scores_truth"}
    by = data.get("by_key_id")
    if not isinstance(by, dict):
        return {"ok": False, "multiplier": 1.0, "reason": "missing_by_key_id"}
    kid = EdgeScoreKey(
        avenue_id=_s(avenue_id),
        gate_id=_s(gate_id).lower(),
        strategy_id=_s(strategy_id),
        edge_family=_s(edge_family),
        symbol_or_product_id=_s(symbol_or_product_id),
        lane_type=_s(lane_type),
    ).to_id()
    rec = by.get(kid)
    if not isinstance(rec, dict):
        return {"ok": False, "multiplier": 1.0, "reason": "no_score_for_key"}
    health = float(rec.get("edge_health_score") or 0.0)
    conf = float(rec.get("rolling_confidence_score") or 0.0)
    status = _s(rec.get("promotion_status") or "")
    # Multiplier: 0.70..1.30. Confidence gates the effect (low confidence => closer to 1.0).
    base = 0.70 + (health / 100.0) * 0.60
    conf_w = _clamp(conf, 0.0, 100.0) / 100.0
    mult = 1.0 + (base - 1.0) * conf_w
    return {
        "ok": True,
        "multiplier": float(max(0.70, min(1.30, mult))),
        "edge_health": health,
        "confidence": conf,
        "promotion_status": status,
        "key_id": kid,
    }

