"""Orchestrator — refresh truth, diagnostics, scoreboard, quality, pass/pause, artifacts."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from trading_ai.first_20.aggregates import aggregate_rows
from trading_ai.first_20.artifacts import (
    build_final_truth,
    build_lessons,
    build_operator_report,
    build_scoreboard,
    lessons_to_txt,
    operator_report_to_txt,
    scoreboard_to_txt,
)
from trading_ai.first_20.constants import (
    P_ADJUSTMENTS,
    P_DIAGNOSTICS,
    P_EDGE_QUALITY,
    P_EXEC_QUALITY,
    P_FINAL_JSON,
    P_FINAL_TXT,
    P_LESSONS_JSON,
    P_LESSONS_TXT,
    P_OPERATOR_JSON,
    P_OPERATOR_TXT,
    P_PASS_DECISION,
    P_PAUSE_REASON,
    P_SCOREBOARD_JSON,
    P_SCOREBOARD_TXT,
    P_TRUTH,
    PhaseStatus,
    default_truth_contract,
)
from trading_ai.first_20.rebuy import load_audit, merge_rebuy_into_truth_pause, save_audit
from trading_ai.first_20.row_builder import build_diagnostic_row
from trading_ai.first_20.rules import (
    caution_from_signals,
    edge_quality_evaluation,
    evaluate_pause,
    execution_quality_evaluation,
    maybe_auto_adjustments,
    pass_decision,
)
from trading_ai.first_20.storage import append_jsonl, ensure_bootstrap, read_json, read_jsonl, write_json, write_text


def _env_active() -> bool:
    return (os.environ.get("FIRST_20_DIAGNOSTIC_PHASE_ACTIVE") or "").strip().lower() in ("1", "true", "yes")


def process_closed_trade(
    trade: Dict[str, Any],
    post_trade_out: Optional[Dict[str, Any]] = None,
    *,
    runtime_root: Optional[Any] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Record one closed trade into first-20 diagnostics when phase is ACTIVE_DIAGNOSTIC and N<20.
    Refreshes all control artifacts. Never raises.
    """
    out: Dict[str, Any] = {"status": "skipped", "reason": "init"}
    try:
        ensure_bootstrap(runtime_root=runtime_root)
        truth = read_json(P_TRUTH, runtime_root=runtime_root) or default_truth_contract()
        phase = str(truth.get("phase_status") or PhaseStatus.NOT_STARTED.value)

        if phase == PhaseStatus.NOT_STARTED.value and not _env_active():
            out = {"status": "skipped", "reason": "phase_not_started"}
            return out

        if phase == PhaseStatus.NOT_STARTED.value and _env_active():
            truth["phase_status"] = PhaseStatus.ACTIVE_DIAGNOSTIC.value
            phase = truth["phase_status"]
            write_json(P_TRUTH, truth, runtime_root=runtime_root)

        if phase == PhaseStatus.PAUSED_REVIEW_REQUIRED.value:
            out = {"status": "skipped", "reason": "phase_paused_review_required"}
            _refresh_artifacts_without_append(runtime_root=runtime_root)
            return out

        if phase != PhaseStatus.ACTIVE_DIAGNOSTIC.value:
            out = {"status": "skipped", "reason": f"phase_{phase}"}
            return out

        rows = read_jsonl(P_DIAGNOSTICS, runtime_root=runtime_root)
        tid = str(trade.get("trade_id") or "").strip()
        if tid and any(str(r.get("trade_id")) == tid for r in rows):
            out = {"status": "skipped", "reason": "duplicate_trade_id", "trade_id": tid}
            _refresh_artifacts_without_append(runtime_root=runtime_root)
            return out

        if len(rows) >= 20:
            out = {"status": "skipped", "reason": "twenty_trades_complete"}
            _refresh_artifacts_without_append(runtime_root=runtime_root)
            return out

        trade_num = len(rows) + 1
        row = build_diagnostic_row(
            trade_number_in_phase=trade_num,
            trade=trade,
            post_trade_out=post_trade_out or {},
            extra=extra,
        )
        append_jsonl(P_DIAGNOSTICS, row, runtime_root=runtime_root)
        rows.append(row)

        truth = _merge_truth_from_rows(truth, rows, runtime_root=runtime_root)
        rebuy = load_audit(runtime_root=runtime_root)
        phase = merge_rebuy_into_truth_pause(rebuy, str(truth.get("phase_status")))
        truth["phase_status"] = phase

        pause, reasons = evaluate_pause(rows=rows, truth=truth, rebuy_audit=rebuy, extra_signals=extra)
        if pause:
            truth["phase_status"] = PhaseStatus.PAUSED_REVIEW_REQUIRED.value
        write_json(P_PAUSE_REASON, {"paused": pause, "reasons": reasons, "trade_count": len(rows)}, runtime_root=runtime_root)

        agg = aggregate_rows(rows)
        caution = caution_from_signals(rows=rows, agg=agg, pause_reasons=reasons, truth=truth)
        ast = dict(truth.get("automation_state") or {})
        ast["caution_level"] = caution
        truth["automation_state"] = ast

        exec_q = execution_quality_evaluation(rows)
        edge_q = edge_quality_evaluation(rows, agg)
        write_json(P_EXEC_QUALITY, exec_q, runtime_root=runtime_root)
        write_json(P_EDGE_QUALITY, edge_q, runtime_root=runtime_root)

        adj_proposals = maybe_auto_adjustments(rows=rows, truth=truth, caution=caution, agg=agg)
        ac = 0
        for line in adj_proposals:
            append_jsonl(P_ADJUSTMENTS, line, runtime_root=runtime_root)
            ac += 1

        sb = build_scoreboard(rows, caution)
        write_json(P_SCOREBOARD_JSON, sb, runtime_root=runtime_root)
        write_text(P_SCOREBOARD_TXT, scoreboard_to_txt(sb), runtime_root=runtime_root)

        lessons = build_lessons(rows, exec_q, edge_q)
        write_json(P_LESSONS_JSON, lessons, runtime_root=runtime_root)
        write_text(P_LESSONS_TXT, lessons_to_txt(lessons), runtime_root=runtime_root)

        pd = pass_decision(
            rows=rows,
            execution_pass=bool(exec_q.get("pass")),
            edge_pass=bool(edge_q.get("pass")),
            caution=caution,
            phase_status=str(truth.get("phase_status")),
            rebuy_audit=rebuy,
            runtime_root=runtime_root,
        )
        write_json(P_PASS_DECISION, pd, runtime_root=runtime_root)

        op = build_operator_report(
            rows=rows,
            truth=truth,
            exec_q=exec_q,
            edge_q=edge_q,
            pass_doc=pd,
            rebuy=rebuy,
            caution=caution,
        )
        write_json(P_OPERATOR_JSON, op, runtime_root=runtime_root)
        write_text(P_OPERATOR_TXT, operator_report_to_txt(op), runtime_root=runtime_root)

        truth["ready_for_next_phase"] = bool(pd.get("passed"))
        if truth["ready_for_next_phase"]:
            truth["exact_reason_if_not_ready"] = ""
        else:
            truth["exact_reason_if_not_ready"] = "; ".join(pd.get("exact_fail_reasons") or ["see_pass_decision"])

        if len(rows) >= 20 and pd.get("passed"):
            truth["phase_status"] = PhaseStatus.PASSED_READY_FOR_NEXT_PHASE.value
        elif len(rows) >= 20 and not pd.get("passed"):
            truth["phase_status"] = PhaseStatus.FAILED_REWORK_REQUIRED.value

        write_json(P_TRUTH, truth, runtime_root=runtime_root)

        ft = build_final_truth(
            phase=str(truth.get("phase_status")),
            rows=rows,
            exec_pass=bool(exec_q.get("pass")),
            edge_pass=bool(edge_q.get("pass")),
            rebuy_clean=bool(rebuy.get("rebuy_contract_clean")),
            pass_doc=pd,
            adjustments_count=_count_adjustments(runtime_root=runtime_root),
        )
        write_json(P_FINAL_JSON, ft, runtime_root=runtime_root)
        why_txt = "\n".join(f"{k}: {v}" for k, v in (ft.get("why_false") or {}).items())
        write_text(
            P_FINAL_TXT,
            "\n".join(
                [
                    "FIRST 20 — FINAL TRUTH",
                    "======================",
                    *[f"{k}: {ft.get(k)}" for k in ft if k not in ("why_false", "honesty")],
                    "",
                    why_txt,
                    "",
                    str(ft.get("honesty")),
                ]
            )
            + "\n",
            runtime_root=runtime_root,
        )

        out = {
            "status": "recorded",
            "trade_number": trade_num,
            "phase_status": truth.get("phase_status"),
            "caution": caution,
            "pause": pause,
            "pass_partial": pd,
        }
        try:
            from trading_ai.operator_truth.live_switch_closure_bundle import write_live_switch_closure_bundle

            out["live_switch_closure"] = write_live_switch_closure_bundle(
                runtime_root=runtime_root,
                trigger_surface="first_20",
                reason="first_20_artifacts_refresh",
            )
        except Exception as exc:
            out["live_switch_closure"] = {"error": str(exc)}
        return out
    except Exception as exc:
        logger.exception("first_20 process_closed_trade failed: %s", exc)
        return {"status": "error", "error": str(exc)}


def _count_adjustments(runtime_root: Optional[Any] = None) -> int:
    return len(read_jsonl(P_ADJUSTMENTS, runtime_root=runtime_root))


def _merge_truth_from_rows(truth: Dict[str, Any], rows: List[Dict[str, Any]], *, runtime_root: Optional[Any]) -> Dict[str, Any]:
    agg = aggregate_rows(rows)
    base = dict(truth)
    base.update(
        {
            "trades_completed": int(agg.get("trades_completed") or 0),
            "wins": int(agg.get("wins") or 0),
            "losses": int(agg.get("losses") or 0),
            "win_rate": float(agg.get("win_rate") or 0),
            "gross_pnl": float(agg.get("gross_pnl") or 0),
            "net_pnl": float(agg.get("net_pnl") or 0),
            "avg_pnl_per_trade": float(agg.get("avg_pnl_per_trade") or 0),
            "expectancy_per_trade": float(agg.get("expectancy_per_trade") or 0),
            "max_consecutive_losses": int(agg.get("max_consecutive_losses") or 0),
            "max_drawdown_seen": float(agg.get("max_drawdown_seen") or 0),
            "duplicate_blocks_seen": int(agg.get("duplicate_blocks_seen") or 0),
            "governance_blocks_seen": int(agg.get("governance_blocks_seen") or 0),
            "adaptive_brakes_seen": int(agg.get("adaptive_brakes_seen") or 0),
            "venue_rejects_seen": int(agg.get("venue_rejects_seen") or 0),
            "partial_failure_count": int(agg.get("partial_failure_count") or 0),
            "logging_failures_seen": int(agg.get("logging_failures_seen") or 0),
            "rebuy_block_failures_seen": int(agg.get("rebuy_block_failures_seen") or 0),
            "strategy_mix": agg.get("strategy_mix") or {},
            "gate_mix": agg.get("gate_mix") or {},
            "avenue_mix": agg.get("avenue_mix") or {},
        }
    )
    return base


def _refresh_artifacts_without_append(*, runtime_root: Optional[Any]) -> None:
    """Recompute artifacts from existing JSONL (e.g. after duplicate skip)."""
    try:
        rows = read_jsonl(P_DIAGNOSTICS, runtime_root=runtime_root)
        truth = read_json(P_TRUTH, runtime_root=runtime_root) or default_truth_contract()
        truth = _merge_truth_from_rows(truth, rows, runtime_root=runtime_root)
        rebuy = load_audit(runtime_root=runtime_root)
        pause, reasons = evaluate_pause(rows=rows, truth=truth, rebuy_audit=rebuy, extra_signals=None)
        if pause:
            truth["phase_status"] = PhaseStatus.PAUSED_REVIEW_REQUIRED.value
        write_json(P_PAUSE_REASON, {"paused": pause, "reasons": reasons, "trade_count": len(rows)}, runtime_root=runtime_root)
        agg = aggregate_rows(rows)
        caution = caution_from_signals(rows=rows, agg=agg, pause_reasons=reasons, truth=truth)
        ast = dict(truth.get("automation_state") or {})
        ast["caution_level"] = caution
        truth["automation_state"] = ast
        exec_q = execution_quality_evaluation(rows)
        edge_q = edge_quality_evaluation(rows, agg)
        write_json(P_EXEC_QUALITY, exec_q, runtime_root=runtime_root)
        write_json(P_EDGE_QUALITY, edge_q, runtime_root=runtime_root)
        sb = build_scoreboard(rows, caution)
        write_json(P_SCOREBOARD_JSON, sb, runtime_root=runtime_root)
        write_text(P_SCOREBOARD_TXT, scoreboard_to_txt(sb), runtime_root=runtime_root)
        lessons = build_lessons(rows, exec_q, edge_q)
        write_json(P_LESSONS_JSON, lessons, runtime_root=runtime_root)
        write_text(P_LESSONS_TXT, lessons_to_txt(lessons), runtime_root=runtime_root)
        pd = pass_decision(
            rows=rows,
            execution_pass=bool(exec_q.get("pass")),
            edge_pass=bool(edge_q.get("pass")),
            caution=caution,
            phase_status=str(truth.get("phase_status")),
            rebuy_audit=rebuy,
            runtime_root=runtime_root,
        )
        write_json(P_PASS_DECISION, pd, runtime_root=runtime_root)
        op = build_operator_report(
            rows=rows,
            truth=truth,
            exec_q=exec_q,
            edge_q=edge_q,
            pass_doc=pd,
            rebuy=rebuy,
            caution=caution,
        )
        write_json(P_OPERATOR_JSON, op, runtime_root=runtime_root)
        write_text(P_OPERATOR_TXT, operator_report_to_txt(op), runtime_root=runtime_root)
        truth["ready_for_next_phase"] = bool(pd.get("passed"))
        truth["exact_reason_if_not_ready"] = (
            "" if truth["ready_for_next_phase"] else "; ".join(pd.get("exact_fail_reasons") or [])
        )
        write_json(P_TRUTH, truth, runtime_root=runtime_root)
        ac = _count_adjustments(runtime_root=runtime_root)
        ft = build_final_truth(
            phase=str(truth.get("phase_status")),
            rows=rows,
            exec_pass=bool(exec_q.get("pass")),
            edge_pass=bool(edge_q.get("pass")),
            rebuy_clean=bool(rebuy.get("rebuy_contract_clean")),
            pass_doc=pd,
            adjustments_count=ac,
        )
        write_json(P_FINAL_JSON, ft, runtime_root=runtime_root)
        try:
            from trading_ai.operator_truth.live_switch_closure_bundle import write_live_switch_closure_bundle

            write_live_switch_closure_bundle(
                runtime_root=runtime_root,
                trigger_surface="first_20_refresh_only",
                reason="first_20_duplicate_skip_or_refresh",
            )
        except Exception:
            pass
    except Exception:
        pass


def activate_diagnostic_phase(runtime_root: Optional[Any] = None) -> Dict[str, Any]:
    ensure_bootstrap(runtime_root=runtime_root)
    truth = read_json(P_TRUTH, runtime_root=runtime_root) or default_truth_contract()
    truth["phase_status"] = PhaseStatus.ACTIVE_DIAGNOSTIC.value
    write_json(P_TRUTH, truth, runtime_root=runtime_root)
    return {"phase_status": truth["phase_status"]}
