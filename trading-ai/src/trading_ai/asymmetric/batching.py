from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from trading_ai.asymmetric.config import AsymmetricConfig, load_asymmetric_config


@dataclass(frozen=True)
class AsymmetricBatchPlan:
    truth_version: str
    venue_id: str
    gate_id: str
    batch_size: int
    allow_single_probe_without_batch: bool
    total_capital_usd: float
    asym_bucket_usd: float
    asym_sub_bucket_usd: float
    reserve_cash_pct: float
    max_batch_deployment_usd: float
    max_position_usd_total_capital: float
    max_position_usd_asym_bucket: float
    max_position_usd: float
    max_open_positions: int
    notes: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _avenue_id_from_venue(venue_id: str) -> str:
    v = (venue_id or "").strip().lower()
    if v == "coinbase":
        return "A"
    if v == "kalshi":
        return "B"
    if v == "tastytrade":
        return "C"
    return "?"


def build_batch_plan(
    *,
    venue_id: str,
    gate_id: str,
    total_capital_usd: float,
    cfg: Optional[AsymmetricConfig] = None,
) -> AsymmetricBatchPlan:
    c = cfg or load_asymmetric_config()
    total = max(0.0, float(total_capital_usd))
    asym_bucket = max(0.0, total * float(c.asym_capital_pct if c.enabled else 0.0))
    asym_bucket *= max(0.0, 1.0 - float(c.asym_reserve_cash_pct))

    avenue_id = _avenue_id_from_venue(venue_id)
    sub_frac = float((c.asym_capital_pct_per_avenue or {}).get(avenue_id, 0.0))
    sub_bucket = max(0.0, asym_bucket * sub_frac)

    max_batch_deploy = max(0.0, sub_bucket * float(c.asym_max_batch_deployment_pct))
    max_pos_total = max(0.0, total * float(c.asym_max_position_pct_of_total))
    max_pos_bucket = max(0.0, sub_bucket * float(c.asym_max_position_pct_of_asym_bucket))
    max_pos = max(0.0, min(max_pos_total if max_pos_total > 0 else max_pos_bucket, max_pos_bucket if max_pos_bucket > 0 else max_pos_total))
    if max_pos <= 0:
        max_pos = min(max_pos_total, max_pos_bucket) if (max_pos_total > 0 and max_pos_bucket > 0) else max(max_pos_total, max_pos_bucket)

    notes: List[str] = []
    notes.append("asymmetric capital is isolated; core capital is not used by this plan")
    notes.append("batch sizing is enforced at sub-bucket level (per avenue within asym bucket)")

    return AsymmetricBatchPlan(
        truth_version="asymmetric_batch_plan_v1",
        venue_id=str(venue_id or "").strip().lower(),
        gate_id=str(gate_id or "").strip(),
        batch_size=int(c.batch_size),
        allow_single_probe_without_batch=bool(c.allow_single_probe_without_batch),
        total_capital_usd=total,
        asym_bucket_usd=float(asym_bucket),
        asym_sub_bucket_usd=float(sub_bucket),
        reserve_cash_pct=float(c.asym_reserve_cash_pct),
        max_batch_deployment_usd=float(max_batch_deploy),
        max_position_usd_total_capital=float(max_pos_total),
        max_position_usd_asym_bucket=float(max_pos_bucket),
        max_position_usd=float(max_pos),
        max_open_positions=int(c.asym_max_open_positions),
        notes=notes,
    )


def validate_batch_plan_or_errors(plan: AsymmetricBatchPlan, *, cfg: Optional[AsymmetricConfig] = None) -> List[str]:
    c = cfg or load_asymmetric_config()
    errs: List[str] = []
    if not c.enabled:
        errs.append("asym_disabled")
    if plan.batch_size <= 0:
        errs.append("invalid_batch_size")
    if plan.asym_bucket_usd < -1e-9 or plan.asym_sub_bucket_usd < -1e-9:
        errs.append("negative_bucket")
    if plan.max_open_positions <= 0:
        errs.append("invalid_max_open_positions")
    if plan.max_batch_deployment_usd < 0:
        errs.append("invalid_max_batch_deployment")
    if plan.max_position_usd <= 0:
        errs.append("max_position_usd_zero")
    if plan.max_position_usd > plan.max_batch_deployment_usd + 1e-9 and plan.max_batch_deployment_usd > 0:
        errs.append("max_position_exceeds_max_batch_deployment")
    if plan.venue_id not in c.venue_allowlist:
        errs.append("venue_not_allowlisted")
    return errs

