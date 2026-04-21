"""Build a single diagnostic row from a closed trade + hub output + explicit first-20 context."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from trading_ai.multi_avenue.lifecycle_hooks import infer_trade_scope


def _truth_level(
    *,
    local_ok: bool,
    remote_ok: bool,
    review_ok: bool,
    pnl_verified: bool,
    failure_codes: List[str],
) -> str:
    if any("INTEGRITY" in x.upper() or "CRITICAL" in x.upper() for x in failure_codes):
        return "DEGRADED"
    if not pnl_verified or not local_ok:
        return "PARTIAL"
    if not remote_ok:
        return "LOCAL_ONLY"
    if not review_ok:
        return "REVIEW_PENDING"
    return "FULL"


def build_diagnostic_row(
    *,
    trade_number_in_phase: int,
    trade: Dict[str, Any],
    post_trade_out: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    post_trade_out = post_trade_out or {}
    extra = extra or {}
    merged = {**extra}
    if isinstance(trade.get("first_20"), dict):
        merged = {**merged, **trade["first_20"]}

    tid = str(trade.get("trade_id") or "").strip()
    aid, gid = infer_trade_scope(trade)
    avenue_id = str(merged.get("avenue_id") or aid or trade.get("avenue_id") or "unknown")
    gate_id = str(merged.get("gate_id") or gid or trade.get("gate_id") or "unknown")
    strategy_id = str(merged.get("strategy_id") or trade.get("strategy_id") or trade.get("strategy_key") or "unknown")

    product = merged.get("product_id") or trade.get("product_id") or trade.get("symbol") or trade.get("market")
    entry_t = merged.get("entry_time") or trade.get("opened_at") or trade.get("entry_time")
    exit_t = merged.get("exit_time") or trade.get("closed_at") or trade.get("exit_time")

    hold_s = merged.get("hold_seconds")
    if hold_s is None and entry_t and exit_t:
        try:
            from datetime import datetime

            e0 = entry_t if isinstance(entry_t, (int, float)) else datetime.fromisoformat(str(entry_t).replace("Z", "+00:00")).timestamp()
            e1 = exit_t if isinstance(exit_t, (int, float)) else datetime.fromisoformat(str(exit_t).replace("Z", "+00:00")).timestamp()
            hold_s = max(0.0, float(e1) - float(e0))
        except Exception:
            hold_s = None
    if hold_s is None:
        hold_s = merged.get("hold_seconds", 0.0)

    result = str(trade.get("result") or "").lower()
    payout = trade.get("payout_dollars")
    gross_pnl = float(merged.get("gross_pnl") if merged.get("gross_pnl") is not None else (payout if payout is not None else 0.0))
    fees = float(merged.get("fees_paid") or trade.get("fees_paid") or 0.0)
    net_pnl = float(merged.get("net_pnl") if merged.get("net_pnl") is not None else (gross_pnl - fees))

    rec = post_trade_out.get("execution_close_reconciliation") or {}
    tq = post_trade_out.get("trade_quality") or {}

    entry_fill = bool(merged.get("entry_fill_confirmed", rec.get("entry_fill_confirmed", True)))
    exit_fill = bool(merged.get("exit_fill_confirmed", rec.get("exit_fill_confirmed", True)))
    pnl_verified = bool(merged.get("pnl_verified", rec.get("pnl_verified", True)))
    local_ok = bool(merged.get("local_write_ok", rec.get("local_write_ok", True)))
    remote_ok = bool(merged.get("remote_write_ok", rec.get("remote_write_ok", True)))
    review_ok = bool(merged.get("review_update_ok", rec.get("review_update_ok", True)))

    fc_raw = merged.get("failure_codes")
    if isinstance(fc_raw, str):
        failure_codes = [fc_raw]
    elif isinstance(fc_raw, list):
        failure_codes = [str(x) for x in fc_raw]
    else:
        failure_codes = []

    if post_trade_out.get("error"):
        failure_codes.append("POST_TRADE_HUB_ERROR")
    if merged.get("logging_failure"):
        failure_codes.append("LOGGING_FAILURE")
    if merged.get("integrity_failure"):
        failure_codes.append("INTEGRITY_FAILURE")
    if merged.get("duplicate_guard_failure"):
        failure_codes.append("DUPLICATE_GUARD")
    if merged.get("emergency_brake_triggered"):
        failure_codes.append("EMERGENCY_BRAKE")

    blocking = str(merged.get("blocking_reason") or "")

    lesson_inf = bool(merged.get("lesson_influence_applied", False))

    row: Dict[str, Any] = {
        "trade_number_in_phase": trade_number_in_phase,
        "trade_id": tid,
        "avenue_id": avenue_id,
        "gate_id": gate_id,
        "strategy_id": strategy_id,
        "product_id": product,
        "symbol": trade.get("symbol"),
        "result": result,
        "entry_time": entry_t,
        "exit_time": exit_t,
        "hold_seconds": float(hold_s or 0.0),
        "entry_fill_confirmed": entry_fill,
        "exit_fill_confirmed": exit_fill,
        "pnl_verified": pnl_verified,
        "gross_pnl": gross_pnl,
        "net_pnl": net_pnl,
        "return_bps": merged.get("return_bps"),
        "fees_paid": fees,
        "slippage_estimate": merged.get("slippage_estimate"),
        "spread_at_entry": merged.get("spread_at_entry"),
        "spread_at_exit": merged.get("spread_at_exit"),
        "candidate_rank_score": merged.get("candidate_rank_score"),
        "lesson_influence_applied": lesson_inf,
        "adaptive_mode_at_entry": merged.get("adaptive_mode_at_entry"),
        "adaptive_mode_post_trade": merged.get("adaptive_mode_post_trade"),
        "duplicate_guard_mode": merged.get("duplicate_guard_mode"),
        "governance_allowed": merged.get("governance_allowed", True),
        "local_write_ok": local_ok,
        "remote_write_ok": remote_ok,
        "review_update_ok": review_ok,
        "ready_for_rebuy": merged.get("ready_for_rebuy"),
        "failure_codes": sorted(set(str(x) for x in failure_codes)),
        "blocking_reason": blocking,
        "truth_level": _truth_level(
            local_ok=local_ok,
            remote_ok=remote_ok,
            review_ok=review_ok,
            pnl_verified=pnl_verified,
            failure_codes=failure_codes,
        ),
        "notes_for_operator": merged.get("notes_for_operator") or "",
        "exit_reason": merged.get("exit_reason") or trade.get("exit_reason"),
        "avenue_metrics": merged.get("avenue_metrics") if isinstance(merged.get("avenue_metrics"), dict) else {},
        "recorded_at_unix": time.time(),
    }
    if tq:
        row["trade_quality_snapshot"] = tq
    return row


def merge_trade_and_context(trade: Dict[str, Any], extra: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out = dict(trade)
    if extra:
        out["_first_20_context"] = extra
    return out
