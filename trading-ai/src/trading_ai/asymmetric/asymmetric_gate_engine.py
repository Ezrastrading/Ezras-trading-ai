from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.asymmetric.config import load_asymmetric_config
from trading_ai.asymmetric.asymmetric_allocator import compute_capital_split, max_position_notional_usd
from trading_ai.asymmetric.batching import (
    AsymmetricBatchPlan,
    build_batch_plan,
    validate_batch_plan_or_errors,
)
from trading_ai.asymmetric.venues.kalshi_b_asym import run_b_asym_cycle
from trading_ai.storage.storage_adapter import LocalStorageAdapter


@dataclass(frozen=True)
class AsymmetricDecision:
    action: str  # NO_TRADE | BATCH_PLANNED
    reason: str
    venue_id: str
    gate_id: str
    trade_type: str  # asymmetric
    capital_bucket_id: str
    avenue_id: str
    batch_size: int
    bucket_usd: float
    sub_bucket_usd: float
    max_per_position_usd: float
    min_ev: float
    evidence: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _no_trade(*, venue_id: str, gate_id: str, reason: str, evidence: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return AsymmetricDecision(
        action="NO_TRADE",
        reason=reason,
        venue_id=venue_id,
        gate_id=gate_id,
        trade_type="asymmetric",
        capital_bucket_id="asymmetric:unknown",
        avenue_id="?",
        batch_size=0,
        bucket_usd=0.0,
        sub_bucket_usd=0.0,
        max_per_position_usd=0.0,
        min_ev=0.0,
        evidence=evidence or {},
    ).to_dict()


def _avenue_id_from_venue(venue_id: str) -> str:
    v = (venue_id or "").strip().lower()
    if v == "coinbase":
        return "A"
    if v == "kalshi":
        return "B"
    if v == "tastytrade":
        return "C"
    return "?"


def asymmetric_gate_cycle(
    *,
    venue_id: str,
    gate_id: str,
    total_capital_usd: float,
    runtime_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Separate system entry point (does not mix into core trade flow).

    For now this is intentionally **fail-closed** and produces a planning-only decision shell.
    Actual scanning/EV models and execution wiring are venue-specific and should be added per-avenue.
    """
    cfg = load_asymmetric_config()
    venue = str(venue_id or "").strip().lower()
    gate = str(gate_id or "").strip().lower()
    avenue_id = _avenue_id_from_venue(venue)
    root = Path(runtime_root) if runtime_root is not None else None
    ad = LocalStorageAdapter(runtime_root=root)

    if not cfg.enabled:
        return _no_trade(venue_id=venue, gate_id=gate, reason="asymmetric_disabled")
    if venue not in cfg.venue_allowlist:
        return _no_trade(
            venue_id=venue,
            gate_id=gate,
            reason="venue_not_allowlisted_for_asymmetric",
            evidence={"venue_allowlist": list(cfg.venue_allowlist)},
        )
    # Venue-specific wiring: B_ASYM is first supported venue.
    if venue == "kalshi" and avenue_id == "B":
        # Enforce explicit gate identity for asym: B_ASYM.
        expect = str(cfg.gate_id_map.get("B") or "B_ASYM").strip().lower()
        if gate and gate != expect:
            return _no_trade(
                venue_id=venue,
                gate_id=gate,
                reason="gate_id_mismatch_for_b_asym",
                evidence={"expected_gate_id": expect, "received_gate_id": gate},
            )
        return run_b_asym_cycle(total_capital_usd=float(total_capital_usd), runtime_root=runtime_root, cfg=cfg)

    split = compute_capital_split(total_capital_usd=total_capital_usd, cfg=cfg)
    bucket = float(split.asymmetric_capital_usd)
    max_pos = float(max_position_notional_usd(asymmetric_bucket_usd=bucket, cfg=cfg))

    # Plan-only shell until venue-specific scanner/EV/execution are wired.
    # Still produce a batch plan artifact so isolation + sizing rails are inspectable.
    plan: AsymmetricBatchPlan = build_batch_plan(
        venue_id=venue,
        gate_id=gate,
        total_capital_usd=float(total_capital_usd),
        cfg=cfg,
    )
    errs = validate_batch_plan_or_errors(plan, cfg=cfg)
    out = AsymmetricDecision(
        action="NO_TRADE",
        reason="asymmetric_engine_not_yet_wired_for_execution" if not errs else "asymmetric_batch_plan_invalid",
        venue_id=venue,
        gate_id=gate,
        trade_type="asymmetric",
        capital_bucket_id=f"asymmetric:{avenue_id}",
        avenue_id=avenue_id,
        batch_size=int(cfg.batch_size),
        bucket_usd=bucket,
        sub_bucket_usd=float(plan.asym_sub_bucket_usd),
        max_per_position_usd=max_pos,
        min_ev=float(cfg.min_ev_usd),
        evidence={
            "capital_split": split.to_dict(),
            "batch_plan": plan.to_dict(),
            "batch_plan_errors": errs,
            "honesty": "This is a planning-only shell; no live execution wiring yet.",
        },
    ).to_dict()
    ad.write_json("data/asymmetric/last_asymmetric_decision.json", out)
    return out

