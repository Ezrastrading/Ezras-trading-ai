"""Mean reversion, continuation pullback, micro momentum — strict filters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from trading_ai.nte.data.feature_engine import FeatureSnapshot


@dataclass
class StrategySignal:
    name: str
    reason: str
    confidence: float


def _scores(store: Any) -> Dict[str, Any]:
    ss = store.load_json("strategy_scores.json")
    avenues = ss.get("avenues") or {}
    cb = avenues.get("coinbase") or {}
    return cb if isinstance(cb, dict) else {}


def pick_strategy(f: FeatureSnapshot, store: Any) -> Optional[StrategySignal]:
    """
    Return highest-weighted strategy that passes structural checks.
    Micro momentum only when regime and z_score are clean.
    """
    if not f.stable or f.spread_pct > 0.0012:
        return None
    if f.mid <= 0:
        return None

    weights = _scores(store)
    mr_w = float((weights.get("mean_reversion") or {}).get("score") or 0.5)
    cp_w = float((weights.get("continuation_pullback") or {}).get("score") or 0.5)
    mm_w = float((weights.get("micro_momentum") or {}).get("score") or 0.5)

    candidates: List[StrategySignal] = []

    # Mean reversion: stretched below MA in range regime
    if f.regime == "range" and f.z_score < -0.9:
        candidates.append(
            StrategySignal(
                "mean_reversion",
                "z_vs_ma20<-0.9 in range",
                min(0.95, 0.55 + mr_w * 0.2),
            )
        )

    # Continuation pullback: trend up + mild pullback to zone
    if f.regime == "trend_up" and -0.35 < f.z_score < 0.15:
        candidates.append(
            StrategySignal(
                "continuation_pullback",
                "uptrend pullback band",
                min(0.92, 0.52 + cp_w * 0.22),
            )
        )

    # Micro momentum: clean tape only
    if (
        f.regime in ("trend_up", "trend_down")
        and abs(f.z_score) < 0.45
        and f.spread_pct < 0.0008
    ):
        candidates.append(
            StrategySignal(
                "micro_momentum",
                "aligned micro trend, tight spread",
                min(0.88, 0.48 + mm_w * 0.25),
            )
        )

    if not candidates:
        return None

    def weight(s: StrategySignal) -> float:
        w = {"mean_reversion": mr_w, "continuation_pullback": cp_w, "micro_momentum": mm_w}.get(
            s.name, 0.5
        )
        return s.confidence * (0.5 + w)

    candidates.sort(key=weight, reverse=True)
    return candidates[0]
