"""
Traceable ratio definitions — universal defaults with explicit scope; gate/avenue overlays separate.

Does **not** silently override: overlays are separate keys (e.g. ``gate.gate_a.per_trade_cap_fraction``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from trading_ai.nte.config.settings import load_nte_settings


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ratio_entry(
    key: str,
    value: Any,
    *,
    scope_type: str,
    scope_id: str,
    source_of_truth: str,
    why_active: str,
    inherited_from: Optional[str] = None,
    override_reason: Optional[str] = None,
    notes: str = "",
) -> Dict[str, Any]:
    return {
        "key": key,
        "value": value,
        "scope_type": scope_type,
        "scope_id": scope_id,
        "source_of_truth": source_of_truth,
        "why_active": why_active,
        "when_last_changed": _iso_now(),
        "changed_by": "code_defaults_and_env",
        "confidence": 1.0 if source_of_truth.startswith("nte_settings") else 0.85,
        "inherited_from": inherited_from,
        "override_reason": override_reason,
        "notes": notes,
    }


@dataclass
class RatioPolicyBundle:
    """Versioned bundle for snapshots and trade context."""

    ratio_policy_version: str
    universal_ratios: Dict[str, Dict[str, Any]]
    avenue_overlays: Dict[str, Dict[str, Dict[str, Any]]]
    gate_overlays: Dict[str, Dict[str, Dict[str, Any]]]
    adaptive_multipliers: Dict[str, Dict[str, Any]]
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ratio_policy_version": self.ratio_policy_version,
            "universal_ratios": self.universal_ratios,
            "avenue_overlays": self.avenue_overlays,
            "gate_overlays": self.gate_overlays,
            "adaptive_multipliers": self.adaptive_multipliers,
            "meta": self.meta,
        }


def build_universal_ratio_policy_bundle(
    *,
    operating_mode: str = "normal",
) -> RatioPolicyBundle:
    """
    Build authoritative in-repo ratio registry from NTE settings + env.

    Env (optional):
    - ``EZRAS_HARD_RESERVE_RATIO`` — fraction of conservative deployable held back (default 0.05)
    - ``EZRAS_SOFT_RESERVE_RATIO`` — additional soft buffer (default 0.02)
    - ``EZRAS_MAX_CONCURRENT_EXPOSURE_RATIO`` — cap (default 0.5)
    """
    nte = load_nte_settings()
    hard_r = float(os.environ.get("EZRAS_HARD_RESERVE_RATIO", os.environ.get("EZRAS_HARD_RESERVE_PCT", "0.05")))
    soft_r = float(os.environ.get("EZRAS_SOFT_RESERVE_RATIO", "0.02"))
    conc = float(os.environ.get("EZRAS_MAX_CONCURRENT_EXPOSURE_RATIO", "0.5"))
    daily_dd = float(os.environ.get("EZRAS_MAX_DAILY_DRAWDOWN_RATIO", str(nte.daily_loss_min)))

    uni: Dict[str, Dict[str, Any]] = {
        "universal.per_trade_cap_fraction": _ratio_entry(
            "universal.per_trade_cap_fraction",
            float(nte.size_pct_max),
            scope_type="universal",
            scope_id="global",
            source_of_truth="nte_settings.size_pct_max",
            why_active="NTE Coinbase position sizing band upper bound",
            notes="Actual per-trade fraction may be lower via executor; this is policy ceiling.",
        ),
        "universal.per_trade_floor_fraction": _ratio_entry(
            "universal.per_trade_floor_fraction",
            float(nte.size_pct_min),
            scope_type="universal",
            scope_id="global",
            source_of_truth="nte_settings.size_pct_min",
            why_active="NTE minimum position fraction",
        ),
        "universal.max_open_positions": _ratio_entry(
            "universal.max_open_positions",
            int(nte.max_open_positions),
            scope_type="universal",
            scope_id="global",
            source_of_truth="nte_settings.max_open_positions",
            why_active="Concurrency cap from NTE",
        ),
        "universal.max_daily_drawdown_ratio": _ratio_entry(
            "universal.max_daily_drawdown_ratio",
            daily_dd,
            scope_type="universal",
            scope_id="global",
            source_of_truth="nte_settings.daily_loss_min/max",
            why_active="Daily loss guard alignment",
        ),
        "universal.hard_reserve_ratio": _ratio_entry(
            "universal.hard_reserve_ratio",
            hard_r,
            scope_type="universal",
            scope_id="global",
            source_of_truth="env_or_default:EZRAS_HARD_RESERVE_RATIO",
            why_active="Capital that must not be counted as live deployable",
        ),
        "universal.soft_reserve_ratio": _ratio_entry(
            "universal.soft_reserve_ratio",
            soft_r,
            scope_type="universal",
            scope_id="global",
            source_of_truth="env_or_default:EZRAS_SOFT_RESERVE_RATIO",
            why_active="Discretionary buffer on top of hard reserve",
        ),
        "universal.max_concurrent_exposure_ratio": _ratio_entry(
            "universal.max_concurrent_exposure_ratio",
            conc,
            scope_type="universal",
            scope_id="global",
            source_of_truth="env_or_default:EZRAS_MAX_CONCURRENT_EXPOSURE_RATIO",
            why_active="Portfolio simultaneous risk envelope",
        ),
        "universal.profit_target_min_ratio": _ratio_entry(
            "universal.profit_target_min_ratio",
            float(nte.tp_min),
            scope_type="universal",
            scope_id="global",
            source_of_truth="nte_settings.tp_min",
            why_active="NTE take-profit band lower",
        ),
        "universal.stop_loss_max_ratio": _ratio_entry(
            "universal.stop_loss_max_ratio",
            float(nte.sl_max),
            scope_type="universal",
            scope_id="global",
            source_of_truth="nte_settings.sl_max",
            why_active="NTE stop-loss band upper",
        ),
        "universal.route_cost_to_edge_min_ratio": _ratio_entry(
            "universal.route_cost_to_edge_min_ratio",
            0.0,
            scope_type="universal",
            scope_id="global",
            source_of_truth="placeholder",
            why_active="Scaffold — set when edge-cost telemetry is wired",
            notes="scaffold_not_live",
        ),
    }

    avenue_coinbase: Dict[str, Dict[str, Any]] = {
        "avenue.coinbase.reserve_buffer_ratio": _ratio_entry(
            "avenue.coinbase.reserve_buffer_ratio",
            hard_r + soft_r,
            scope_type="avenue",
            scope_id="coinbase",
            source_of_truth="derived:hard+soft_reserve",
            why_active="Venue spot: combine universal buffers for Coinbase Avenue A",
            inherited_from="universal.hard_reserve_ratio+universal.soft_reserve_ratio",
        ),
        "avenue.coinbase.fee_model_ref": _ratio_entry(
            "avenue.coinbase.fee_model_ref",
            "coinbase_advanced_trade",
            scope_type="avenue",
            scope_id="coinbase",
            source_of_truth="venue_adapter",
            why_active="Identifier only — fees applied in execution layer",
        ),
    }

    gate_a: Dict[str, Dict[str, Any]] = {
        "gate.gate_a.per_trade_cap_fraction": _ratio_entry(
            "gate.gate_a.per_trade_cap_fraction",
            float(nte.size_pct_max),
            scope_type="gate",
            scope_id="gate_a",
            source_of_truth="nte_settings (Gate A / core NTE)",
            why_active="Gate A uses NTE sizing unless operator overrides elsewhere",
            inherited_from="universal.per_trade_cap_fraction",
        ),
        "gate.gate_a.trailing_ratio_ref": _ratio_entry(
            "gate.gate_a.trailing_ratio_ref",
            float(nte.trail_trigger),
            scope_type="gate",
            scope_id="gate_a",
            source_of_truth="nte_settings.trail_trigger",
            why_active="Gate A trail trigger from NTE",
        ),
    }
    gate_b: Dict[str, Dict[str, Any]] = {
        "gate.gate_b.momentum_safe_deployable_fraction": _ratio_entry(
            "gate.gate_b.momentum_safe_deployable_fraction",
            min(0.35, float(nte.size_pct_max)),
            scope_type="gate",
            scope_id="gate_b",
            source_of_truth="derived_conservative",
            why_active="Kalshi momentum path — conservative cap vs NTE max single fraction",
            notes="Advisory default; live Gate B may read adaptive OS separately.",
        ),
    }

    adaptive = {
        "adaptive.normal.size_multiplier": _ratio_entry(
            "adaptive.normal.size_multiplier",
            1.0,
            scope_type="adaptive",
            scope_id=operating_mode,
            source_of_truth="adaptive_os_placeholder",
            why_active=f"Operating mode={operating_mode}",
            notes="Replace with live adaptive OS multiplier when wired to gate entry",
        ),
    }

    return RatioPolicyBundle(
        ratio_policy_version="universal_ratio_policy_v1",
        universal_ratios=uni,
        avenue_overlays={"coinbase": avenue_coinbase},
        gate_overlays={"gate_a": gate_a, "gate_b": gate_b},
        adaptive_multipliers=adaptive,
        meta={
            "generated_at": _iso_now(),
            "inheritance_model": "explicit_keys_no_silent_merge",
            "honest_status": {
                "route_cost_to_edge_min_ratio": "scaffold_only",
                "adaptive_multipliers": "partially_wired_to_adaptive_os_elsewhere",
            },
        },
    )
