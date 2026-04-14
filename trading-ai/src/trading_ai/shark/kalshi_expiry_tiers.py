"""
Kalshi near-resolution expiry tiers — configurable windows and priority (A > B > C).

Env (minutes, comma-separated min,max):
  KALSHI_TIER_A=5,10
  KALSHI_TIER_B=10,30
  KALSHI_TIER_C=30,60
  KALSHI_TIER_PRIORITY=A,B,C
  KALSHI_TIER_C_MIN_EDGE_ADVANTAGE=0.02   # Tier C HV only if no A/B in batch OR edge beats best A/B by this much

Doctrine / anti_forced_trade (Kalshi NEAR_RESOLUTION_HV — tiered vs phase default 0.02):
  KALSHI_TIER_A_MIN_EDGE=0.0080
  KALSHI_TIER_B_MIN_EDGE=0.0150
  KALSHI_TIER_C_MIN_EDGE=0.0200
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Sequence, Tuple

# Half-open [lo, hi) for A and B; Tier C uses inclusive upper bound so 60m resolves to C.
_DEFAULT_A = (5.0, 10.0)
_DEFAULT_B = (10.0, 30.0)
_DEFAULT_C = (30.0, 60.0)
_DEFAULT_PRIORITY: Tuple[str, ...] = ("A", "B", "C")


def _parse_pair(raw: str, default: Tuple[float, float]) -> Tuple[float, float]:
    s = (raw or "").strip()
    if not s:
        return default
    parts = [p.strip() for p in s.split(",") if p.strip()]
    if len(parts) != 2:
        return default
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return default


def _parse_priority(raw: str) -> Tuple[str, ...]:
    s = (raw or "").strip().upper()
    if not s:
        return _DEFAULT_PRIORITY
    out: List[str] = []
    for p in s.split(","):
        x = p.strip().upper()
        if x in ("A", "B", "C") and x not in out:
            out.append(x)
    for x in ("A", "B", "C"):
        if x not in out:
            out.append(x)
    return tuple(out)


def load_tier_bounds_minutes() -> Dict[str, Tuple[float, float]]:
    return {
        "A": _parse_pair(os.environ.get("KALSHI_TIER_A") or "", _DEFAULT_A),
        "B": _parse_pair(os.environ.get("KALSHI_TIER_B") or "", _DEFAULT_B),
        "C": _parse_pair(os.environ.get("KALSHI_TIER_C") or "", _DEFAULT_C),
    }


def load_tier_priority() -> Tuple[str, ...]:
    return _parse_priority(os.environ.get("KALSHI_TIER_PRIORITY") or "")


def tier_c_min_edge_advantage() -> float:
    raw = (os.environ.get("KALSHI_TIER_C_MIN_EDGE_ADVANTAGE") or "0.02").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.02


def classify_kalshi_expiry_tier(time_to_resolution_seconds: float) -> Optional[str]:
    """
    Map time-to-resolution to A / B / C using configured windows (minutes).
    A and B: half-open [lo, hi) in minutes. C: [lo, hi] inclusive on the upper minute bound.
    First matching tier in KALSHI_TIER_PRIORITY wins at boundaries shared between labels.
    """
    if time_to_resolution_seconds <= 0:
        return None
    m = time_to_resolution_seconds / 60.0
    bounds = load_tier_bounds_minutes()
    priority = load_tier_priority()

    def in_tier(name: str) -> bool:
        lo, hi = bounds.get(name, (0.0, 0.0))
        if hi <= lo:
            return False
        if name == "C":
            return lo <= m <= hi
        return lo <= m < hi

    for name in priority:
        if name in bounds and in_tier(name):
            return name
    return None


def _parse_min_edge_env(key: str, default: float) -> float:
    raw = (os.environ.get(key) or "").strip()
    if not raw:
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


def kalshi_doctrine_base_min_edge(time_to_resolution_seconds: float) -> Optional[float]:
    """
    Base minimum edge for doctrine / anti_forced_trade on Kalshi NEAR_RESOLUTION_HV, by expiry tier.
    Outside configured A/B/C windows → ``None`` (caller keeps phase ``effective_min_edge``, typically 0.02+).
    """
    tier = classify_kalshi_expiry_tier(time_to_resolution_seconds)
    if tier == "A":
        return _parse_min_edge_env("KALSHI_TIER_A_MIN_EDGE", 0.0080)
    if tier == "B":
        return _parse_min_edge_env("KALSHI_TIER_B_MIN_EDGE", 0.0150)
    if tier == "C":
        return _parse_min_edge_env("KALSHI_TIER_C_MIN_EDGE", 0.0200)
    return None


def kalshi_hv_effective_min_edge_for_doctrine(
    time_to_resolution_seconds: float,
    *,
    idle_capital_over_6h: bool,
    drawdown_over_25pct: bool,
) -> Optional[float]:
    """Apply same idle (+15%) and drawdown (+20%) wideners as :func:`risk_context.effective_min_edge` to tier base."""
    base = kalshi_doctrine_base_min_edge(time_to_resolution_seconds)
    if base is None:
        return None
    from trading_ai.shark.risk_context import effective_min_edge

    return effective_min_edge(
        base,
        idle_capital_widen=idle_capital_over_6h,
        drawdown_over_25pct=drawdown_over_25pct,
    )


def tier_priority_rank(tier: Optional[str]) -> int:
    """Lower = scan / execute first (A before B before C). Unknown tiers sort last."""
    if not tier:
        return 99
    order = load_tier_priority()
    try:
        return order.index(tier)
    except ValueError:
        return 50


def resolution_speed_score_kalshi_tiers(time_to_resolution_seconds: float) -> float:
    """
    Replaces the generic ~1h resolution bucket for Kalshi (see scorer.resolution_speed_score).
    Tier A (5–10m) → 1.0, B → ~0.82, C → ~0.62; outside configured windows → tail score.
    """
    t = classify_kalshi_expiry_tier(time_to_resolution_seconds)
    if t == "A":
        return 1.0
    if t == "B":
        return 0.82
    if t == "C":
        return 0.62
    m = time_to_resolution_seconds / 60.0
    bounds = load_tier_bounds_minutes()
    a_lo, _ = bounds.get("A", _DEFAULT_A)
    _, c_hi = bounds.get("C", _DEFAULT_C)
    if m > c_hi:
        return 0.25
    if m < a_lo:
        return 0.45
    return 0.35


def filter_kalshi_hv_tier_c_when_ab_available(
    batch: Sequence[Tuple[object, object]],
) -> List[Tuple[object, object]]:
    """
    When the batch has Kalshi NEAR_RESOLUTION_HV in Tier A or B, drop Tier-C HV rows unless
    their edge beats the best A/B edge by at least ``tier_c_min_edge_advantage()``.
    Non-HV rows and non-Kalshi rows are unchanged.
    """
    from trading_ai.shark.models import HuntType

    delta = tier_c_min_edge_advantage()

    def _is_kalshi_hv_c(scored, m) -> bool:
        if (getattr(m, "outlet", None) or "").lower() != "kalshi":
            return False
        hunts = getattr(scored, "hunts", None) or []
        if not any(getattr(h, "hunt_type", None) == HuntType.NEAR_RESOLUTION_HV for h in hunts):
            return False
        t = classify_kalshi_expiry_tier(float(getattr(m, "time_to_resolution_seconds", 0) or 0))
        return t == "C"

    def _is_kalshi_hv_ab(scored, m) -> bool:
        if (getattr(m, "outlet", None) or "").lower() != "kalshi":
            return False
        hunts = getattr(scored, "hunts", None) or []
        if not any(getattr(h, "hunt_type", None) == HuntType.NEAR_RESOLUTION_HV for h in hunts):
            return False
        t = classify_kalshi_expiry_tier(float(getattr(m, "time_to_resolution_seconds", 0) or 0))
        return t in ("A", "B")

    ab_edges: List[float] = []
    for scored, m in batch:
        if _is_kalshi_hv_ab(scored, m):
            ab_edges.append(float(getattr(scored, "edge_size", 0) or 0))

    best_ab = max(ab_edges) if ab_edges else None
    if best_ab is None:
        return list(batch)

    out: List[Tuple[object, object]] = []
    for scored, m in batch:
        if not _is_kalshi_hv_c(scored, m):
            out.append((scored, m))
            continue
        edge = float(getattr(scored, "edge_size", 0) or 0)
        if edge >= best_ab + delta:
            out.append((scored, m))
        # else: drop weak Tier C when A/B HV exists in batch
    return out
