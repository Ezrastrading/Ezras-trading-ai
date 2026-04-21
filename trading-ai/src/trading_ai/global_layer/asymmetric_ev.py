from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Sequence

from trading_ai.global_layer.asymmetric_models import (
    AsymmetricEVResult,
    AsymmetricEVScenario,
    ConfidenceBand,
)


@dataclass(frozen=True)
class AsymmetricEVCosts:
    entry_cost_usd: float
    fees_usd: float
    slippage_usd: float

    def total_costs(self) -> float:
        return float(max(0.0, self.entry_cost_usd) + max(0.0, self.fees_usd) + max(0.0, self.slippage_usd))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _entropy(probs: Sequence[float]) -> float:
    ps = [p for p in probs if p > 1e-15]
    if not ps:
        return 0.0
    return float(-sum(p * math.log(p) for p in ps))


def _skew_ratio(payouts: Sequence[float]) -> float:
    if not payouts:
        return 0.0
    mx = max(payouts)
    med = sorted(payouts)[len(payouts) // 2]
    if abs(med) <= 1e-12:
        return float("inf") if mx > 0 else 0.0
    return float(mx / med) if med != 0 else float("inf")


def compute_asymmetric_ev(
    *,
    scenarios: Sequence[AsymmetricEVScenario],
    costs: AsymmetricEVCosts,
    quality_inputs: Optional[Dict[str, Any]] = None,
) -> AsymmetricEVResult:
    """
    Universal EV model:

      EV_gross = Σ p_i * payout_i
      EV_net   = EV_gross - total_costs

    payouts are in USD (cash returned), not "profit"; costs capture stake+fees+slippage.
    """
    sc = list(scenarios)
    probs = [_clamp01(float(s.probability)) for s in sc]
    sprob = sum(probs)
    if sprob > 1e-9 and abs(sprob - 1.0) > 1e-6:
        probs = [p / sprob for p in probs]

    payouts = [float(s.payout_usd) for s in sc]
    ev_gross = sum(p * pay for p, pay in zip(probs, payouts))
    ev_net = float(ev_gross - costs.total_costs())
    entry = float(max(0.0, costs.entry_cost_usd))
    ev_per_dollar = float(ev_net / entry) if entry > 1e-9 else 0.0

    exp_mult = float((ev_gross / entry) if entry > 1e-9 else 0.0)
    skew = _skew_ratio(payouts)
    ent = _entropy(probs)

    # Tail dependency heuristic: share of EV from top-1 payout scenario
    tail = 0.0
    if sc and entry > 0:
        contribs = [p * pay for p, pay in zip(probs, payouts)]
        top = max(contribs) if contribs else 0.0
        denom = sum(contribs) if sum(contribs) > 1e-12 else 0.0
        tail = float(top / denom) if denom > 0 else 0.0

    qi = dict(quality_inputs or {})
    calibration = float(qi.get("calibration_score") or 0.5)
    evidence = float(qi.get("evidence_score") or 0.5)
    liquidity = float(qi.get("liquidity_score") or 0.5)
    model_quality = max(0.0, min(1.0, 0.45 * calibration + 0.35 * evidence + 0.20 * liquidity))

    if model_quality >= 0.75:
        cb = ConfidenceBand.HIGH.value
    elif model_quality >= 0.45:
        cb = ConfidenceBand.MEDIUM.value
    else:
        cb = ConfidenceBand.LOW.value

    return AsymmetricEVResult(
        truth_version="asymmetric_ev_result_v1",
        expected_value_gross_usd=float(ev_gross),
        expected_value_net_usd=float(ev_net),
        expected_multiple=float(exp_mult),
        ev_per_dollar=float(ev_per_dollar),
        payoff_skew_ratio=float(skew if math.isfinite(skew) else 1e9),
        tail_dependency_score=float(max(0.0, min(1.0, tail))),
        confidence_band=str(cb),
        scenario_entropy=float(ent),
        model_quality_score=float(model_quality),
        scenario_breakdown=[
            {
                "scenario_id": s.scenario_id,
                "label": s.label,
                "probability": float(p),
                "payout_usd": float(s.payout_usd),
                "ev_contribution_usd": float(p * float(s.payout_usd)),
            }
            for s, p in zip(sc, probs)
        ],
        costs=costs.to_dict(),
    )

