"""
Deterministic Gate B tuning resolution — layered defaults, conservative clamps, honest calibration labels.

Does not loosen risk vs baseline env-loaded :class:`GateBConfig` unless a future policy explicitly allows it.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, List, Optional

from trading_ai.shark.coinbase_spot.gate_b_config import GateBConfig, load_gate_b_config_from_env


def _account_bucket(deployable_usd: Optional[float]) -> str:
    if deployable_usd is None or deployable_usd <= 0:
        return "unknown"
    if deployable_usd < 2500.0:
        return "small"
    if deployable_usd < 25_000.0:
        return "medium"
    return "large"


def resolve_gate_b_tuning_artifact(
    *,
    deployable_quote_usd: Optional[float],
    measured_slippage_bps: Optional[float],
    baseline_config: Optional[GateBConfig] = None,
    assumed_slippage_bps: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Returns a JSON-serializable snapshot for operator status and audits.

    **Safety:** reductions in capacity / concurrency only — no wider stops vs baseline.
    """
    base = baseline_config or load_gate_b_config_from_env()
    cfg = replace(base)
    clamps: List[str] = []
    policy_sources: List[str] = ["env:GateBConfig_defaults", "gate_b_tuning_resolver_v1"]

    bucket = _account_bucket(deployable_quote_usd)
    slip_in = measured_slippage_bps if measured_slippage_bps is not None else assumed_slippage_bps

    # Account-size: only tighten (reduce concurrency / top-k on small deployable)
    if bucket == "small":
        prev_k = int(cfg.momentum_top_k)
        prev_p = int(cfg.max_simultaneous_positions)
        cfg.momentum_top_k = max(1, prev_k - 1)
        cfg.max_simultaneous_positions = max(1, min(prev_p, 3))
        if cfg.momentum_top_k != prev_k:
            clamps.append("momentum_top_k_reduced_for_small_account_bucket")
        if cfg.max_simultaneous_positions != prev_p:
            clamps.append("max_simultaneous_positions_capped_for_small_account_bucket")
        policy_sources.append("account_size_bucket:small")

    # Slippage: increase profit exit buffer only (more conservative execution assumption)
    if slip_in is not None and slip_in > 35.0:
        bump = min(0.003, 0.0005 + (slip_in - 35.0) * 1e-5)
        new_buf = min(0.02, float(cfg.profit_exit_slippage_buffer_pct) + bump)
        if new_buf > cfg.profit_exit_slippage_buffer_pct:
            cfg.profit_exit_slippage_buffer_pct = new_buf
            clamps.append("profit_exit_slippage_buffer_pct_raised_for_high_slippage_assumption")
            policy_sources.append("slippage_assumption:conservative_buffer")

    # Truthful labels: never claim "full" calibration without measured slippage + known deployable.
    calibration_level = "baseline_env_only"
    calibration_truth_detail = "no_measured_slippage_and_or_no_deployable_quote"
    if deployable_quote_usd is not None and deployable_quote_usd > 0 and measured_slippage_bps is not None:
        calibration_level = "full_measured_slippage_and_deployable"
        calibration_truth_detail = "deployable_quote_usd_and_measured_slippage_bps_present"
    elif deployable_quote_usd is not None and deployable_quote_usd > 0:
        calibration_level = "account_size_only_measured_slippage_unknown"
        calibration_truth_detail = "deployable_present_measured_slippage_absent_conservative_defaults"
    elif measured_slippage_bps is not None:
        calibration_level = "slippage_only_deployable_unknown"
        calibration_truth_detail = "measured_slippage_present_deployable_absent"
    elif slip_in is not None and assumed_slippage_bps is not None:
        calibration_level = "assumed_slippage_buffer_only"
        calibration_truth_detail = "both_assumed_slippage_inputs_supplied_not_measured"
    elif slip_in is not None:
        calibration_level = "partial_slippage_input"
        calibration_truth_detail = "single_slippage_hint_without_full_pair"

    return {
        "truth_version": "gate_b_tuning_resolution_v2",
        "account_size_bucket": bucket,
        "deployable_quote_usd": deployable_quote_usd,
        "measured_slippage_bps": measured_slippage_bps,
        "assumed_slippage_bps": assumed_slippage_bps,
        "slippage_input_used": slip_in,
        "tuning_inputs_visible": {
            "deployable_quote_usd": deployable_quote_usd,
            "measured_slippage_bps": measured_slippage_bps,
            "assumed_slippage_bps": assumed_slippage_bps,
            "baseline_from_env": True,
        },
        "calibration_truth_detail": calibration_truth_detail,
        "liquidity_assumptions_note": (
            "Scanner/engine rows supply liquidity fields; provenance is labeled in liquidity_gate / data_quality outputs."
        ),
        "calibration_level": calibration_level,
        "policy_source": policy_sources,
        "clamp_reasons": clamps,
        "selected_tuning": {
            "profit_zone_min_pct": cfg.profit_zone_min_pct,
            "profit_zone_max_pct": cfg.profit_zone_max_pct,
            "profit_exit_slippage_buffer_pct": cfg.profit_exit_slippage_buffer_pct,
            "trailing_stop_from_peak_pct": cfg.trailing_stop_from_peak_pct,
            "hard_stop_from_entry_pct": cfg.hard_stop_from_entry_pct,
            "max_hold_sec": cfg.max_hold_sec,
            "max_simultaneous_positions": cfg.max_simultaneous_positions,
            "momentum_top_k": cfg.momentum_top_k,
            "min_liquidity_score": cfg.min_liquidity_score,
            "max_spread_bps": cfg.max_spread_bps,
            "min_volume_24h_usd": cfg.min_volume_24h_usd,
        },
        "honesty": (
            "Baseline Gate B env values are conservative starting points. "
            "This resolver only applies conservative clamps — never more aggressive live behavior."
        ),
    }
