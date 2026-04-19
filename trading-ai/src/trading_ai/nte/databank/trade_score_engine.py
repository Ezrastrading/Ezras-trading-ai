"""Per-trade scoring: execution, edge, discipline, composite quality (not PnL alone)."""

from __future__ import annotations

import math
from typing import Any, Dict, Mapping


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _num(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def _bool(x: Any) -> bool:
    return bool(x)


def score_execution(event: Mapping[str, Any]) -> float:
    """
    0–100: slippage, maker/taker, partials, stale cancel, fill time.
    Higher = cleaner execution vs intent.
    """
    entry_slip = abs(_num(event.get("entry_slippage_bps")))
    exit_slip = abs(_num(event.get("exit_slippage_bps")))
    slip_total = entry_slip + exit_slip
    # Slippage curve: 0 bps -> 100, 80+ bps -> ~20
    slip_component = _clamp(100.0 - slip_total * 1.0)

    mt = str(event.get("maker_taker") or "unknown").lower()
    if mt == "maker":
        mt_score = 100.0
    elif mt == "taker":
        mt_score = 72.0
    else:
        mt_score = 80.0

    partials = int(_num(event.get("partial_fill_count")))
    partial_score = _clamp(100.0 - min(40.0, partials * 12.0))

    stale = _bool(event.get("stale_cancelled"))
    stale_score = 35.0 if stale else 100.0

    fill_sec = _num(event.get("fill_seconds"))
    # Penalize very slow fills (> 120s)
    fill_score = _clamp(100.0 - max(0.0, (fill_sec - 30.0) * 0.35))

    weighted = (
        slip_component * 0.38
        + mt_score * 0.18
        + partial_score * 0.18
        + stale_score * 0.14
        + fill_score * 0.12
    )
    return round(_clamp(weighted), 2)


def score_edge(event: Mapping[str, Any]) -> float:
    """
    0–100: expected vs realized edge, fees, route signal, regime fit (heuristic).
    """
    exp_net = _num(event.get("expected_net_edge_bps"))
    net_pnl = _num(event.get("net_pnl"))
    gross = _num(event.get("gross_pnl"))
    fees = abs(_num(event.get("fees_paid")))

    # Directional viability: positive expected edge should correlate with outcome (soft).
    sign_exp = 1.0 if exp_net >= 0 else -1.0
    # Scale PnL to a rough bps proxy if notional unknown — use magnitude of net_pnl vs fees
    viability = 50.0
    if fees > 1e-9:
        viability = _clamp(50.0 + 25.0 * math.copysign(1.0, net_pnl) * min(2.0, abs(net_pnl) / fees))
    else:
        viability = _clamp(50.0 + min(40.0, net_pnl))

    alignment = 70.0
    if exp_net > 0 and net_pnl >= 0:
        alignment = 95.0
    elif exp_net > 0 > net_pnl:
        alignment = 45.0
    elif exp_net <= 0 and net_pnl <= 0:
        alignment = 75.0
    else:
        alignment = 60.0

    ra = event.get("route_a_score")
    rb = event.get("route_b_score")
    route_quality = 75.0
    if ra is not None and rb is not None:
        try:
            ra_f, rb_f = float(ra), float(rb)
            chosen = str(event.get("route_chosen") or "").upper()
            if chosen == "A":
                route_quality = _clamp(60.0 + (ra_f - rb_f) * 5.0)
            elif chosen == "B":
                route_quality = _clamp(60.0 + (rb_f - ra_f) * 5.0)
        except (TypeError, ValueError):
            pass

    regime = str(event.get("regime") or "unknown")
    regime_fit = 80.0 if regime not in ("unknown", "", "chaos") else 65.0

    weighted = viability * 0.28 + alignment * 0.32 + route_quality * 0.22 + regime_fit * 0.18
    _ = sign_exp  # reserved for future signed edge modeling
    return round(_clamp(weighted), 2)


def score_discipline(event: Mapping[str, Any]) -> float:
    """0–100: rules, degraded mode, health, anomalies."""
    base = 100.0 if _bool(event.get("discipline_ok")) else 35.0
    if _bool(event.get("degraded_mode")):
        base -= 18.0
    health = str(event.get("health_state") or "ok").lower()
    if health not in ("ok", "healthy", "nominal"):
        base -= 22.0
    flags = event.get("anomaly_flags") or []
    if isinstance(flags, list):
        base -= min(40.0, len(flags) * 12.0)
    return round(_clamp(base), 2)


def compute_trade_quality(
    execution_score: float,
    edge_score: float,
    discipline_score: float,
) -> float:
    """Composite quality — explicit weights; PnL not used here."""
    q = execution_score * 0.38 + edge_score * 0.34 + discipline_score * 0.28
    return round(_clamp(q), 2)


def compute_scores_for_trade(event: Mapping[str, Any]) -> Dict[str, float]:
    ex = score_execution(event)
    ed = score_edge(event)
    di = score_discipline(event)
    tq = compute_trade_quality(ex, ed, di)
    return {
        "execution_score": ex,
        "edge_score": ed,
        "discipline_score": di,
        "trade_quality_score": tq,
    }


def suggest_reward_penalty_deltas(scores: Mapping[str, Any], event: Mapping[str, Any]) -> Dict[str, float]:
    """Hook for reward engine — small deltas from quality vs baseline 50."""
    tq = _num(scores.get("trade_quality_score"), 50.0)
    reward = max(0.0, (tq - 50.0) / 50.0 * 2.0)
    penalty = max(0.0, (50.0 - tq) / 50.0 * 2.0)
    if event.get("degraded_mode"):
        penalty += 0.5
    return {"reward_delta": round(reward, 4), "penalty_delta": round(penalty, 4)}
