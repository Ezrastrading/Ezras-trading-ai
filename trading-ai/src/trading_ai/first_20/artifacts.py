"""Scoreboard, lessons, operator report, final truth text/json."""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List

from trading_ai.first_20.aggregates import aggregate_rows, rolling_last_k
from trading_ai.first_20.constants import CautionLevel, PhaseStatus


def build_scoreboard(rows: List[Dict[str, Any]], caution: str) -> Dict[str, Any]:
    agg = aggregate_rows(rows)
    net_pnls = list(agg.get("net_pnls") or [])
    last5 = rolling_last_k(net_pnls, 5)
    wins5 = [r for r in rows[-5:] if str(r.get("result")) == "win"]
    roll_wr = (len(wins5) / len(rows[-5:])) if len(rows) >= 5 else float(agg.get("win_rate") or 0)

    holds = [float(r.get("hold_seconds") or 0) for r in rows]
    fees = [float(r.get("fees_paid") or 0) for r in rows]
    slips = [float(r.get("slippage_estimate") or 0) for r in rows if r.get("slippage_estimate") is not None]

    strat_b = {k: {"trades": v, "net_pnl": sum(float(r.get("net_pnl") or 0) for r in rows if str(r.get("strategy_id")) == k)} for k, v in (agg.get("strategy_mix") or {}).items()}
    gate_b = {k: {"trades": v, "net_pnl": sum(float(r.get("net_pnl") or 0) for r in rows if str(r.get("gate_id")) == k)} for k, v in (agg.get("gate_mix") or {}).items()}
    ave_b = {k: {"trades": v, "net_pnl": sum(float(r.get("net_pnl") or 0) for r in rows if str(r.get("avenue_id")) == k)} for k, v in (agg.get("avenue_mix") or {}).items()}

    exits = Counter(str(r.get("exit_reason") or "unknown") for r in rows)

    best = max((float(r.get("net_pnl") or 0), r.get("trade_id")) for r in rows) if rows else (0.0, None)
    worst = min((float(r.get("net_pnl") or 0), r.get("trade_id")) for r in rows) if rows else (0.0, None)

    return {
        "cumulative_trades": int(agg.get("trades_completed") or 0),
        "cumulative_net_pnl": float(agg.get("net_pnl") or 0),
        "rolling_5_trade_pnl": sum(last5),
        "rolling_5_trade_expectancy": (sum(last5) / len(last5)) if last5 else 0.0,
        "rolling_win_rate": roll_wr,
        "average_hold_seconds": (sum(holds) / len(holds)) if holds else 0.0,
        "average_fees": (sum(fees) / len(fees)) if fees else 0.0,
        "average_slippage_estimate": (sum(slips) / len(slips)) if slips else None,
        "strategy_breakdown": strat_b,
        "gate_breakdown": gate_b,
        "avenue_breakdown": ave_b,
        "exit_reason_distribution": dict(exits),
        "best_trade": {"trade_id": best[1], "net_pnl": best[0]},
        "worst_trade": {"trade_id": worst[1], "net_pnl": worst[0]},
        "current_caution_level": caution,
        "aggregate": {k: agg[k] for k in ("win_rate", "expectancy_per_trade", "max_drawdown_seen") if k in agg},
    }


def scoreboard_to_txt(sb: Dict[str, Any]) -> str:
    lines = [
        "FIRST 20 — SCOREBOARD",
        "=======================",
        f"Cumulative trades: {sb.get('cumulative_trades')}",
        f"Cumulative net PnL: {sb.get('cumulative_net_pnl')}",
        f"Rolling 5-trade PnL: {sb.get('rolling_5_trade_pnl')}",
        f"Rolling 5-trade expectancy: {sb.get('rolling_5_trade_expectancy')}",
        f"Rolling win rate (last 5): {sb.get('rolling_win_rate')}",
        f"Avg hold (s): {sb.get('average_hold_seconds')}",
        f"Avg fees: {sb.get('average_fees')}",
        f"Avg slippage (est): {sb.get('average_slippage_estimate')}",
        f"Caution: {sb.get('current_caution_level')}",
        "",
        "Best / worst:",
        f"  {sb.get('best_trade')}",
        f"  {sb.get('worst_trade')}",
        "",
    ]
    return "\n".join(lines) + "\n"


def milestone_tags(n: int) -> List[int]:
    out = []
    for m in (5, 10, 15, 20):
        if n == m:
            out.append(m)
    return out


def build_lessons(rows: List[Dict[str, Any]], exec_q: Dict[str, Any], edge_q: Dict[str, Any]) -> Dict[str, Any]:
    agg = aggregate_rows(rows)
    n = len(rows)
    real: List[str] = []
    noisy: List[str] = []
    if n >= 8 and float(agg.get("expectancy_per_trade") or 0) > 0:
        real.append("Sample expectancy slightly positive — still low-N.")
    if n < 8:
        noisy.append("Win/loss streaks are statistically noisy at this N.")

    exec_fails = [r.get("trade_id") for r in rows if "LOGGING_FAILURE" in (r.get("failure_codes") or [])]
    edge_fails = [r.get("trade_id") for r in rows if float(r.get("net_pnl") or 0) < 0 and not any(x in (r.get("failure_codes") or []) for x in ("LOGGING_FAILURE", "INTEGRITY_FAILURE"))]

    dont_repeat: List[str] = []
    for r in rows:
        if "DUPLICATE_GUARD" in str(r.get("failure_codes")):
            dont_repeat.append(f"duplicate_guard:{r.get('trade_id')}")

    weight_later: List[str] = []
    for sid, c in (agg.get("strategy_mix") or {}).items():
        if int(c) >= 3:
            subpnl = sum(float(r.get("net_pnl") or 0) for r in rows if str(r.get("strategy_id")) == str(sid))
            if subpnl > 0:
                weight_later.append(f"strategy {sid} net positive in-sample")

    uncertain = [
        "Direction of true edge vs fees/slippage without longer sample.",
        "Lesson influence effect size (ranking vs exit) without A/B isolation.",
    ]
    more_samples = [
        "Need >20 live trades for stable gate/strategy attribution.",
        "Per-avenue fee/slippage curves need venue-specific calibration.",
    ]

    body = {
        "what_seems_real": real,
        "what_seems_noisy": noisy,
        "what_failed_because_of_execution": exec_fails,
        "what_failed_because_of_poor_edge": edge_fails,
        "what_should_not_be_repeated": dont_repeat,
        "what_might_deserve_more_weight_later": weight_later,
        "what_is_still_uncertain": uncertain,
        "what_requires_more_samples": more_samples,
        "execution_quality_ref": {k: exec_q.get(k) for k in ("score_0_to_100", "pass") if k in exec_q},
        "edge_quality_ref": {k: edge_q.get(k) for k in ("score_0_to_100", "pass") if k in edge_q},
        "milestones": {str(m): "snapshot" for m in milestone_tags(n)},
    }
    return body


def lessons_to_txt(doc: Dict[str, Any]) -> str:
    lines = ["FIRST 20 — OPERATIONAL LESSONS", "================================"]
    for key in (
        "what_seems_real",
        "what_seems_noisy",
        "what_failed_because_of_execution",
        "what_failed_because_of_poor_edge",
        "what_should_not_be_repeated",
        "what_might_deserve_more_weight_later",
        "what_is_still_uncertain",
        "what_requires_more_samples",
    ):
        lines.append(f"{key}:")
        for item in doc.get(key) or []:
            lines.append(f"  - {item}")
        lines.append("")
    return "\n".join(lines) + "\n"


def build_operator_report(
    *,
    rows: List[Dict[str, Any]],
    truth: Dict[str, Any],
    exec_q: Dict[str, Any],
    edge_q: Dict[str, Any],
    pass_doc: Dict[str, Any],
    rebuy: Dict[str, Any],
    caution: str,
) -> Dict[str, Any]:
    sb = build_scoreboard(rows, caution)
    strat = sb.get("strategy_breakdown") or {}
    best_s = max(strat.items(), key=lambda kv: kv[1].get("net_pnl", 0.0)) if strat else None
    worst_s = min(strat.items(), key=lambda kv: kv[1].get("net_pnl", 0.0)) if strat else None

    lesson_trades = sum(1 for r in rows if r.get("lesson_influence_applied"))

    return {
        "1_safe_to_continue": bool(str(caution) not in (CautionLevel.RED.value,) and not pass_doc.get("failed")),
        "2_system_behaving_honestly": bool(exec_q.get("pass")),
        "3_execution_layer_clean": bool(exec_q.get("pass")),
        "4_edge_real_weak_or_uncertain": "uncertain"
        if len(rows) < 12
        else ("weak" if not edge_q.get("pass") else "supported_by_sample"),
        "5_strongest_gate_strategy_avenue": {
            "strategy": best_s[0] if best_s else None,
            "strategy_stats": best_s[1] if best_s else None,
        },
        "6_should_be_paused": list((truth.get("automation_state") or {}).get("paused_strategy_ids") or []),
        "7_lessons_affecting_decisions": {
            "trades_with_lesson_flag": lesson_trades,
            "fraction": (lesson_trades / len(rows)) if rows else 0.0,
        },
        "8_change_before_trade_21": pass_doc.get("recommended_size_policy_for_next_phase"),
        "pass_decision": {k: pass_doc.get(k) for k in ("passed", "failed", "exact_fail_reasons")},
        "rebuy_audit": rebuy,
        "caution_level": caution,
    }


def operator_report_to_txt(r: Dict[str, Any]) -> str:
    lines = [
        "FIRST 20 — OPERATOR REPORT",
        "==========================",
        f"1. Safe to continue? {r.get('1_safe_to_continue')}",
        f"2. System honest? {r.get('2_system_behaving_honestly')}",
        f"3. Execution clean? {r.get('3_execution_layer_clean')}",
        f"4. Edge: {r.get('4_edge_real_weak_or_uncertain')}",
        f"5. Strongest: {r.get('5_strongest_gate_strategy_avenue')}",
        f"6. Paused list: {r.get('6_should_be_paused')}",
        f"7. Lessons: {r.get('7_lessons_affecting_decisions')}",
        f"8. Change before trade 21: {r.get('8_change_before_trade_21')}",
        "",
    ]
    return "\n".join(lines) + "\n"


def build_final_truth(
    *,
    phase: str,
    rows: List[Dict[str, Any]],
    exec_pass: bool,
    edge_pass: bool,
    rebuy_clean: bool,
    pass_doc: Dict[str, Any],
    adjustments_count: int,
) -> Dict[str, Any]:
    """Runtime-proven flags are False unless evidence supports them (honesty rule)."""
    n = len(rows)
    why: Dict[str, str] = {}

    active = phase in (PhaseStatus.ACTIVE_DIAGNOSTIC.value, PhaseStatus.PAUSED_REVIEW_REQUIRED.value)
    if not active:
        why["FIRST_20_PHASE_ACTIVE"] = "First-20 diagnostic capture not in progress (not ACTIVE or PAUSED)."
    runtime_proven = bool(n >= 1 and phase != PhaseStatus.NOT_STARTED.value)
    if not runtime_proven:
        why["FIRST_20_RUNTIME_PROVEN"] = "No diagnostic rows recorded or phase never left NOT_STARTED."

    exec_proven = bool(exec_pass and n >= 8)
    if not exec_proven:
        why["FIRST_20_EXECUTION_QUALITY_PROVEN"] = "Requires execution_pass and minimum sample (8) for stable execution read."

    edge_proven = bool(edge_pass and n >= 15)
    if not edge_proven:
        why["FIRST_20_EDGE_EVALUATION_PROVEN"] = "Requires edge_pass and minimum sample (15) — edge vs noise not proven earlier."

    rebuy_proven = bool(rebuy_clean and n >= 5)
    if not rebuy_proven:
        why["FIRST_20_REBUY_CONTRACT_PROVEN"] = "Rebuy cleanliness must hold across multiple completed trades (min 5)."

    guard_proven = bool(adjustments_count > 0)
    if not guard_proven:
        why["FIRST_20_AUTO_ADJUST_GUARDRAILS_PROVEN"] = "No audited adjustment lines yet — engine not exercised in production."

    ready = bool(pass_doc.get("passed"))
    if not ready:
        why["FIRST_20_READY_FOR_NEXT_PHASE"] = "Pass decision false — see first_20_pass_decision.json."

    flags = {
        "FIRST_20_PHASE_ACTIVE": active,
        "FIRST_20_RUNTIME_PROVEN": runtime_proven,
        "FIRST_20_EXECUTION_QUALITY_PROVEN": exec_proven,
        "FIRST_20_EDGE_EVALUATION_PROVEN": edge_proven,
        "FIRST_20_REBUY_CONTRACT_PROVEN": rebuy_proven,
        "FIRST_20_AUTO_ADJUST_GUARDRAILS_PROVEN": guard_proven,
        "FIRST_20_READY_FOR_NEXT_PHASE": ready,
    }
    why_only = {k: why[k] for k in flags if not flags[k] and k in why}
    return {
        **flags,
        "why_false": why_only,
        "honesty": "TRUE only when predicates hold; each FALSE boolean has an entry in why_false.",
    }

