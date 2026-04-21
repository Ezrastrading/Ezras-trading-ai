from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from trading_ai.asymmetric.config import AsymmetricConfig, load_asymmetric_config
from trading_ai.asymmetric.batching import AsymmetricBatchPlan, build_batch_plan


@dataclass(frozen=True)
class AsymmetricSizingDecision:
    recommended_notional_usd: float
    loss_at_risk_usd: float
    asym_bucket_fraction: float
    total_capital_fraction: float
    batch_fraction: float
    position_tier: str  # skip|micro|tiny|small
    concentration_warning: Optional[str]
    sizing_reason: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def new_batch_id(prefix: str = "asym") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def compute_asymmetric_position_size(
    *,
    plan: AsymmetricBatchPlan,
    requested_notional_usd: float,
    cfg: Optional[AsymmetricConfig] = None,
    open_positions_count: int = 0,
) -> AsymmetricSizingDecision:
    c = cfg or load_asymmetric_config()
    req = max(0.0, float(requested_notional_usd))

    if open_positions_count >= int(c.asym_max_open_positions):
        return AsymmetricSizingDecision(
            recommended_notional_usd=0.0,
            loss_at_risk_usd=0.0,
            asym_bucket_fraction=0.0,
            total_capital_fraction=0.0,
            batch_fraction=0.0,
            position_tier="skip",
            concentration_warning="max_open_positions_reached",
            sizing_reason="ASYM_MAX_OPEN_POSITIONS reached; fail-closed.",
        )

    cap_pos = float(plan.max_position_usd)
    cap_batch = float(plan.max_batch_deployment_usd)
    if cap_pos <= 0 or cap_batch <= 0:
        return AsymmetricSizingDecision(
            recommended_notional_usd=0.0,
            loss_at_risk_usd=0.0,
            asym_bucket_fraction=0.0,
            total_capital_fraction=0.0,
            batch_fraction=0.0,
            position_tier="skip",
            concentration_warning="invalid_plan_caps",
            sizing_reason="Batch plan invalid caps; fail-closed.",
        )

    # Enforce micro sizing: never exceed caps; allow caller to request smaller.
    approved = min(req if req > 0 else cap_pos, cap_pos)

    # Heuristic tiers by fraction of total capital.
    total = max(1e-9, float(plan.total_capital_usd))
    frac_total = approved / total
    if frac_total <= 0:
        tier = "skip"
    elif frac_total <= 0.001:
        tier = "micro"
    elif frac_total <= 0.003:
        tier = "tiny"
    else:
        tier = "small"

    warn = None
    if approved >= cap_pos - 1e-9:
        warn = "at_max_position_cap"
    if plan.batch_size > 0 and approved * plan.batch_size > cap_batch + 1e-9:
        # Caller can still size per-position lower; return lower approved so batch fits.
        approved = max(0.0, cap_batch / float(plan.batch_size))
        warn = "reduced_to_fit_batch_cap"

    sub_bucket = max(1e-9, float(plan.asym_sub_bucket_usd))
    batch_frac = (approved * float(plan.batch_size)) / sub_bucket if sub_bucket > 0 else 0.0
    pos_frac_bucket = approved / sub_bucket if sub_bucket > 0 else 0.0

    return AsymmetricSizingDecision(
        recommended_notional_usd=float(approved),
        loss_at_risk_usd=float(approved),  # defined-risk assumption (may be refined per instrument)
        asym_bucket_fraction=float(pos_frac_bucket),
        total_capital_fraction=float(frac_total),
        batch_fraction=float(batch_frac),
        position_tier=tier,
        concentration_warning=warn,
        sizing_reason="Asymmetric sizing: micro optionality; enforce per-position and per-batch caps.",
    )


def build_asymmetric_plan_for_venue(
    *,
    venue_id: str,
    gate_id: str,
    total_capital_usd: float,
    cfg: Optional[AsymmetricConfig] = None,
) -> AsymmetricBatchPlan:
    c = cfg or load_asymmetric_config()
    return build_batch_plan(venue_id=venue_id, gate_id=gate_id, total_capital_usd=total_capital_usd, cfg=c)

