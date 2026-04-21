"""Pause, pass, caution, and limited auto-adjustment rules (explicit, audited)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from trading_ai.first_20.aggregates import aggregate_rows, consecutive_failure_counts, max_failure_repeat, rolling_last_k
from trading_ai.first_20.constants import CautionLevel, PhaseStatus
from trading_ai.first_20.storage import max_drawdown_config, operator_ack_fresh, operator_ack_hours


def evaluate_pause(
    *,
    rows: List[Dict[str, Any]],
    truth: Dict[str, Any],
    rebuy_audit: Dict[str, Any],
    extra_signals: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, List[str]]:
    """Hard pause conditions — any True → PAUSED_REVIEW_REQUIRED."""
    reasons: List[str] = []
    extra = extra_signals or {}

    if int(consecutive_failure_counts(rows, "INTEGRITY")) >= 3:
        reasons.append("three_consecutive_integrity_failures")
    if int(consecutive_failure_counts(rows, "LOGGING_FAILURE")) >= 3:
        reasons.append("three_consecutive_logging_failures")

    if bool(rebuy_audit.get("any_rebuy_before_log_completion")):
        reasons.append("rebuy_before_log_completion")
    if bool(rebuy_audit.get("any_rebuy_before_exit_truth")):
        reasons.append("rebuy_before_exit_truth")

    if bool(extra.get("duplicate_rebuy_violation")):
        reasons.append("duplicate_rebuy_violation")

    if bool(extra.get("execution_success_false_after_entry")):
        reasons.append("execution_success_false_after_entry_dangerous")

    if bool(extra.get("emergency_brake_triggered")):
        reasons.append("emergency_brake_triggered")

    dd_limit = max_drawdown_config()
    agg = aggregate_rows(rows)
    if float(agg.get("max_drawdown_seen") or 0) > dd_limit:
        reasons.append(f"max_drawdown_exceeds_first20_limit_usd_{dd_limit}")

    max_rep, sig = max_failure_repeat(rows)
    if max_rep >= 3 and sig:
        reasons.append(f"same_failure_three_times:{sig}")

    n = len(rows)
    if n >= 12:
        exp = float(agg.get("expectancy_per_trade") or 0)
        net_pnls = agg.get("net_pnls") or []
        neg = sum(1 for x in net_pnls if x < 0)
        if exp < 0 and neg >= int(0.65 * n):
            reasons.append("net_expectancy_clearly_negative_with_evidence")

    vr = 0
    for r in rows[-5:]:
        fc = r.get("failure_codes") or []
        if isinstance(fc, list) and any("VENUE" in str(x).upper() or "REJECT" in str(x).upper() for x in fc):
            vr += 1
    if vr >= 3:
        reasons.append("venue_rejects_or_malformed_orders_repeated")

    # Duplicate guard contract break in-row
    for r in rows:
        fc = r.get("failure_codes") or []
        if isinstance(fc, list) and any("DUPLICATE_GUARD" in str(x).upper() for x in fc):
            reasons.append("duplicate_guard_failure_recorded")
            break

    return bool(reasons), sorted(set(reasons))


def caution_from_signals(
    *,
    rows: List[Dict[str, Any]],
    agg: Dict[str, Any],
    pause_reasons: List[str],
    truth: Dict[str, Any],
) -> str:
    if pause_reasons:
        return CautionLevel.RED.value
    ast = truth.get("automation_state") or {}
    if str(ast.get("caution_level") or "") == CautionLevel.RED.value:
        return CautionLevel.RED.value

    n = len(rows)
    net_pnls: List[float] = list(agg.get("net_pnls") or [])
    last5 = rolling_last_k(net_pnls, 5)
    roll5 = sum(last5) if last5 else 0.0

    integ_streak = consecutive_failure_counts(rows, "INTEGRITY")
    log_streak = consecutive_failure_counts(rows, "LOGGING_FAILURE")
    if integ_streak >= 1 or log_streak >= 1:
        return CautionLevel.YELLOW.value

    partial = int(agg.get("partial_failure_count") or 0)
    if partial >= 2:
        return CautionLevel.ORANGE.value

    if n >= 8:
        exp = float(agg.get("expectancy_per_trade") or 0)
        if exp < 0:
            return CautionLevel.ORANGE.value

    if roll5 < 0 and n >= 5:
        return CautionLevel.YELLOW.value

    dd = float(agg.get("max_drawdown_seen") or 0)
    if dd > max_drawdown_config() * 0.5:
        return CautionLevel.YELLOW.value

    return CautionLevel.GREEN.value


def execution_quality_evaluation(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    checks: Dict[str, Any] = {}
    n = max(len(rows), 1)
    fills_ok = sum(1 for r in rows if r.get("entry_fill_confirmed") and r.get("exit_fill_confirmed"))
    checks["fills_confirmed_ratio"] = fills_ok / n
    slip = [float(r.get("slippage_estimate") or 0) for r in rows if r.get("slippage_estimate") is not None]
    checks["slippage_mean_if_present"] = sum(slip) / len(slip) if slip else None
    checks["slippage_acceptable"] = (sum(slip) / len(slip) < 50.0) if slip else True  # placeholder threshold
    checks["latency_acceptable"] = True  # requires venue metrics; default honest unknown
    log_complete = sum(1 for r in rows if "LOGGING_FAILURE" not in " ".join(r.get("failure_codes") or []))
    checks["logging_complete_ratio"] = log_complete / n
    recon_clean = sum(1 for r in rows if r.get("truth_level") in ("FULL", "LOCAL_ONLY", "REVIEW_PENDING"))
    checks["reconciliation_cleanish_ratio"] = recon_clean / n
    remote = sum(1 for r in rows if r.get("remote_write_ok"))
    checks["remote_sync_ratio"] = remote / n
    dup_ok = not any("DUPLICATE_GUARD" in str(r.get("failure_codes")) for r in rows)
    checks["no_duplicate_rebuy_bug"] = dup_ok
    gov_ok = sum(1 for r in rows if r.get("governance_allowed", True))
    checks["governance_behaved_ratio"] = gov_ok / n

    penalties = 0
    if checks["fills_confirmed_ratio"] < 0.9:
        penalties += 25
    if checks["logging_complete_ratio"] < 0.85:
        penalties += 25
    if not dup_ok:
        penalties += 40
    if checks["remote_sync_ratio"] < 0.75:
        penalties += 10

    score = max(0, min(100, 100 - penalties))
    weaknesses: List[str] = []
    if checks["fills_confirmed_ratio"] < 0.9:
        weaknesses.append("Some entry/exit fills not confirmed in diagnostic rows.")
    if checks["logging_complete_ratio"] < 0.85:
        weaknesses.append("Logging or reconciliation gaps across trades.")
    if not dup_ok:
        weaknesses.append("Duplicate / rebuy guard fired at least once.")
    next_step = "Restore execution integrity (fills, logging, remote sync) before trusting edge."
    if score >= 70 and dup_ok:
        next_step = "Maintain execution hygiene; continue sampling edge under diagnostic sizing."

    return {
        "checks": checks,
        "score_0_to_100": score,
        "pass": bool(score >= 70 and dup_ok and checks["logging_complete_ratio"] >= 0.75),
        "exact_weaknesses": weaknesses,
        "exact_recommended_next_step": next_step,
    }


def edge_quality_evaluation(rows: List[Dict[str, Any]], agg: Dict[str, Any]) -> Dict[str, Any]:
    n = len(rows)
    exp = float(agg.get("expectancy_per_trade") or 0)
    wr = float(agg.get("win_rate") or 0)
    strat_mix = agg.get("strategy_mix") or {}
    weaknesses: List[str] = []
    if n >= 8 and exp < 0:
        weaknesses.append("Negative sample expectancy with enough trades to suspect weak edge.")
    if wr < 0.35 and n >= 10:
        weaknesses.append("Win rate persistently low for sample size.")
    # per-strategy underperformance
    under: List[str] = []
    for sid, c in strat_mix.items():
        if int(c) >= 3:
            sub = [r for r in rows if str(r.get("strategy_id")) == str(sid)]
            nets = [float(r.get("net_pnl") or 0) for r in sub]
            if sum(nets) < 0:
                under.append(str(sid))
    if under:
        weaknesses.append(f"Strategies net-negative over repeated samples: {', '.join(under)}")

    lesson_hits = sum(1 for r in rows if r.get("lesson_influence_applied"))
    checks = {
        "positive_expectancy_sample": exp > 0,
        "reasonable_win_loss_profile": wr >= 0.3 or n < 8,
        "lessons_applied_trades": lesson_hits,
    }
    penalties = 0
    if n >= 8 and exp < 0:
        penalties += 35
    if under:
        penalties += 20
    if wr < 0.3 and n >= 10:
        penalties += 15

    score = max(0, min(100, 100 - penalties))
    next_step = "Continue diagnostic with tight size; separate execution issues from edge."
    if score < 50:
        next_step = "Pause scaling; review gates/strategies with negative contribution."

    return {
        "checks": checks,
        "underperforming_strategies": under,
        "score_0_to_100": score,
        "pass": bool(score >= 55 and not (n >= 12 and exp < 0 and len(weaknesses) >= 2)),
        "exact_weaknesses": weaknesses,
        "exact_recommended_next_step": next_step,
    }


def pass_decision(
    *,
    rows: List[Dict[str, Any]],
    execution_pass: bool,
    edge_pass: bool,
    caution: str,
    phase_status: str,
    rebuy_audit: Dict[str, Any],
    runtime_root: Any,
) -> Dict[str, Any]:
    n = len(rows)
    pass_reasons: List[str] = []
    fail_reasons: List[str] = []

    if n >= 20:
        pass_reasons.append("twenty_completed_diagnostic_rows")
    else:
        fail_reasons.append("fewer_than_twenty_completed_trades")

    for r in rows:
        fcs = [str(x) for x in (r.get("failure_codes") or [])]
        if any("PARTIAL_FAILURE" in str(x).upper() for x in fcs) and not r.get("local_write_ok"):
            fail_reasons.append("unresolved_partial_failure")
            break

    if any("LOGGING_FAILURE" in (r.get("failure_codes") or []) for r in rows):
        fail_reasons.append("critical_logging_gap")

    if not bool(rebuy_audit.get("rebuy_contract_clean", True)):
        fail_reasons.append("rebuy_contract_not_clean")

    if not execution_pass:
        fail_reasons.append("execution_quality_pass_false")

    if str(caution) == CautionLevel.RED.value:
        fail_reasons.append("caution_RED")

    if bool(rebuy_audit.get("any_rebuy_before_log_completion")) or bool(rebuy_audit.get("any_rebuy_before_exit_truth")):
        fail_reasons.append("rebuy_timing_violation")

    ack_ok = operator_ack_fresh(runtime_root=runtime_root, max_age_hours=operator_ack_hours())
    if not ack_ok:
        fail_reasons.append("operator_truth_artifacts_not_refreshed")

    extra = {}
    try:
        from trading_ai.first_20.storage import read_json
        from trading_ai.first_20.constants import P_TRUTH

        tdoc = read_json(P_TRUTH, runtime_root=runtime_root) or {}
        if str(tdoc.get("phase_status")) == PhaseStatus.PAUSED_REVIEW_REQUIRED.value:
            extra["adaptive_halt_note"] = "phase_paused_requires_manual_resolution"
    except Exception:
        pass

    if phase_status == PhaseStatus.PAUSED_REVIEW_REQUIRED.value:
        fail_reasons.append("phase_currently_paused")

    # Edge / expectancy gate (not PnL profit requirement)
    agg = aggregate_rows(rows)
    if n >= 15:
        exp = float(agg.get("expectancy_per_trade") or 0)
        if exp < -1e-6:
            fail_reasons.append("expectancy_clearly_negative_after_review_window")

    edge_ok_review = edge_pass or (n < 12)
    if not edge_ok_review:
        fail_reasons.append("edge_quality_not_acceptable_at_sample")

    # Dedupe fail_reasons
    fail_reasons = sorted(set(fail_reasons))

    passed = (
        n >= 20
        and not fail_reasons
        and execution_pass
        and str(caution) != CautionLevel.RED.value
        and bool(rebuy_audit.get("rebuy_contract_clean", True))
        and ack_ok
    )

    if passed:
        pass_reasons.append("system_coherence_and_execution_honesty")
        pass_reasons.append("operator_evidence_ack_current")

    manual_review = str(caution) in (CautionLevel.ORANGE.value, CautionLevel.RED.value) or not ack_ok

    next_phase = "graduated_diagnostic_or_operator_defined"
    if not passed:
        next_phase = "remain_in_first20_or_rework"

    size_policy = "keep_base_size_or_reduce_if_caution_ORANGE"
    if passed:
        size_policy = "modest_step_up_only_with_operator_ack_and_monitoring"
    elif str(caution) == CautionLevel.RED.value:
        size_policy = "halt_new_risk_until_review"

    return {
        "passed": bool(passed),
        "failed": bool(not passed),
        "manual_review_required": bool(manual_review or not passed),
        "exact_pass_reasons": pass_reasons,
        "exact_fail_reasons": fail_reasons,
        "next_safe_phase": next_phase,
        "recommended_size_policy_for_next_phase": size_policy,
        "operator_ack_satisfied": ack_ok,
        "edge_pass_eval": edge_pass,
        "execution_pass_eval": execution_pass,
        "extra": extra,
    }


def maybe_auto_adjustments(
    *,
    rows: List[Dict[str, Any]],
    truth: Dict[str, Any],
    caution: str,
    agg: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Conservative adjustments only — returns proposed audit lines (caller appends)."""
    from datetime import datetime, timezone

    proposals: List[Dict[str, Any]] = []
    ts = datetime.now(timezone.utc).isoformat()
    n = len(rows)
    ast = dict(truth.get("automation_state") or {})

    if str(caution) in (CautionLevel.ORANGE.value, CautionLevel.RED.value):
        prev = float(ast.get("size_multiplier") or 1.0)
        newv = max(0.25, prev * 0.5)
        if newv < prev:
            proposals.append(
                {
                    "ts": ts,
                    "trigger_trade_number": n,
                    "trigger_reason": "caution_elevated",
                    "adjustment_type": "reduce_size_multiplier",
                    "previous_value": prev,
                    "new_value": newv,
                    "evidence_used": {"caution": caution, "last5_pnl_sum": sum(rolling_last_k(list(agg.get("net_pnls") or []), 5))},
                    "auto_or_operator": "auto",
                    "reversible": True,
                }
            )
            ast["size_multiplier"] = newv

    # Pause worst strategy if clearly negative and enough samples
    strat = agg.get("strategy_mix") or {}
    for sid, c in strat.items():
        if int(c) >= 4:
            sub = [r for r in rows if str(r.get("strategy_id")) == str(sid)]
            if len(sub) >= 4 and sum(float(r.get("net_pnl") or 0) for r in sub) < 0:
                paused = list(ast.get("paused_strategy_ids") or [])
                if sid not in paused:
                    proposals.append(
                        {
                            "ts": ts,
                            "trigger_trade_number": n,
                            "trigger_reason": "strategy_net_negative_repeated",
                            "adjustment_type": "pause_strategy_id",
                            "previous_value": None,
                            "new_value": sid,
                            "evidence_used": {"strategy_id": sid, "trades": len(sub), "net": sum(float(r.get("net_pnl") or 0) for r in sub)},
                            "auto_or_operator": "auto",
                            "reversible": True,
                        }
                    )
                    paused.append(str(sid))
                    ast["paused_strategy_ids"] = paused

    if str(caution) == CautionLevel.RED.value:
        ast["operator_review_required"] = True
        proposals.append(
            {
                "ts": ts,
                "trigger_trade_number": n,
                "trigger_reason": "caution_RED",
                "adjustment_type": "require_operator_review",
                "previous_value": False,
                "new_value": True,
                "evidence_used": {"rows": n},
                "auto_or_operator": "auto",
                "reversible": True,
            }
        )

    truth["automation_state"] = ast
    return proposals
