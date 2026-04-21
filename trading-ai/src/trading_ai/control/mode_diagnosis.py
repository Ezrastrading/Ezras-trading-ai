"""Structured diagnosis after emergency brake or mode transition — operator-readable."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from trading_ai.control.emergency_brake import BrakeEvaluation
from trading_ai.control.operating_mode_types import OperatingMode, OperatingSnapshot


def build_diagnosis_artifact(
    *,
    brake: BrakeEvaluation,
    snap: OperatingSnapshot,
    prior_mode: OperatingMode,
    new_mode: OperatingMode,
    gate_a_failing: Optional[bool] = None,
    gate_b_failing: Optional[bool] = None,
) -> Dict[str, Any]:
    likely: List[str] = []
    conf = 0.5
    if brake.reasons:
        if any("loss" in r for r in brake.reasons):
            likely.append("performance_degradation_or_edge_decay")
            conf = 0.65
        if any("drawdown" in r for r in brake.reasons):
            likely.append("capital_drawdown_exceeded_risk_tolerance")
            conf = max(conf, 0.7)
        if any("slippage" in r or "execution" in r for r in brake.reasons):
            likely.append("execution_or_liquidity_distortion")
            conf = max(conf, 0.68)
        if any("reconciliation" in r or "databank" in r for r in brake.reasons):
            likely.append("infrastructure_or_truth_pipeline_issue")
            conf = max(conf, 0.85)
        if any("governance" in r or "blocked" in r for r in brake.reasons):
            likely.append("governance_or_intent_mismatch")
            conf = max(conf, 0.6)

    healthy: List[str] = []
    if snap.liquidity_health > 0.6:
        healthy.append("liquidity_conditions_acceptable")
    if snap.slippage_health > 0.6:
        healthy.append("slippage_not_critically_degraded")

    rec: List[str] = []
    if new_mode == OperatingMode.HALTED:
        rec.append("pause_new_risk; run full reconciliation; review last N trades post-fee")
        rec.append("do_not_scale_until_anomalies_clear")
    elif new_mode in (OperatingMode.DEFENSIVE, OperatingMode.CAUTIOUS):
        rec.append("reduce_size; tighten entry filters; verify regime alignment")

    return {
        "what_likely_went_wrong": likely or ["insufficient_data_explicit_causes"],
        "diagnosis_confidence": round(conf, 3),
        "reduce_pause_or_change": rec,
        "still_healthy_signals": healthy,
        "gate_a_underperforming": gate_a_failing,
        "gate_b_underperforming": gate_b_failing,
        "loss_attribution_hints": _attribution_hints(snap),
        "operator_summary": _one_line(brake, new_mode),
    }


def _attribution_hints(snap: OperatingSnapshot) -> List[str]:
    hints: List[str] = []
    if snap.market_chop_score > 0.7:
        hints.append("possible_regime_mismatch_chop")
    if snap.slippage_health < 0.5:
        hints.append("slippage_drag")
    if snap.liquidity_health < 0.5:
        hints.append("liquidity_stress")
    return hints


def _one_line(brake: BrakeEvaluation, mode: OperatingMode) -> str:
    if not brake.reasons:
        return f"Operating mode {mode.value}; no emergency triggers this cycle."
    return f"Mode {mode.value} — triggers: " + "; ".join(brake.reasons[:5])


def merge_diagnosis_into_ceo_payload(
    diagnosis: Mapping[str, Any],
    ceo_base: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    out = dict(ceo_base or {})
    out["emergency_diagnosis"] = dict(diagnosis)
    return out
