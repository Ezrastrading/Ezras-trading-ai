"""
STRICT 20-TRADE LIVE VALIDATION (trade20_validation_v1)

Evidence-first, fail-closed, fee-aware, gate-aware, venue-aware.

This module is intentionally strict about:
- only consuming **real closed-trade records** (no alert inference)
- never fabricating missing economics
- recording missing/contradictory truth as integrity evidence
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from trading_ai.storage.storage_adapter import LocalStorageAdapter

TRUTH_VERSION = "trade20_validation_v1"
WINDOW_TARGET = 20

P_STATE = "data/validation/trade20_validation_state.json"
P_REPORT_JSON = "data/validation/trade20_validation_report.json"
P_REPORT_TXT = "data/validation/trade20_validation_report.txt"

P_REVIEW_INPUT = "data/review/trade20_ceo_review_input.json"
P_LESSONS = "data/learning/trade20_lessons.json"
P_GATE_ACTIONS = "data/control/trade20_gate_actions.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _s(x: Any) -> str:
    return str(x or "").strip()


def _sf(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def _si(x: Any) -> Optional[int]:
    if x is None:
        return None
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def _ratio(num: float, den: float) -> Optional[float]:
    if den == 0:
        return None
    return num / den


def _as_list_unique(xs: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for x in xs:
        s = str(x).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _extract_timestamp(trade: Dict[str, Any], keys: Sequence[str]) -> str:
    for k in keys:
        v = trade.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return ""


def _derive_hold_seconds(open_ts: str, close_ts: str, explicit: Any) -> Optional[float]:
    hs = _sf(explicit)
    if hs is not None and hs >= 0:
        return hs
    if not open_ts or not close_ts:
        return None
    try:
        t0 = datetime.fromisoformat(open_ts.replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(close_ts.replace("Z", "+00:00"))
        if t0.tzinfo is None:
            t0 = t0.replace(tzinfo=timezone.utc)
        if t1.tzinfo is None:
            t1 = t1.replace(tzinfo=timezone.utc)
        return max(0.0, (t1 - t0).total_seconds())
    except Exception:
        return None


def _infer_gate_and_venue(trade: Dict[str, Any]) -> Tuple[str, str]:
    # Gate is explicit; venue can be venue_id/avenue_id/venue/avenue.
    gate = _s(trade.get("gate_id") or trade.get("gate") or trade.get("gate"))
    venue = _s(
        trade.get("venue_id")
        or trade.get("venue")
        or trade.get("avenue_id")
        or trade.get("avenue")
        or trade.get("venue_name")
        or trade.get("avenue_name")
    )
    return (gate or "unknown_gate"), (venue or "unknown_venue")


def _infer_symbol(trade: Dict[str, Any]) -> str:
    return (
        _s(trade.get("symbol"))
        or _s(trade.get("product_id"))
        or _s(trade.get("product"))
        or _s(trade.get("market"))
        or _s(trade.get("ticker"))
        or "unknown_symbol"
    )


def _exit_reason(trade: Dict[str, Any]) -> str:
    return _s(trade.get("exit_reason") or trade.get("close_reason") or trade.get("failure_reason") or "")


def _side(trade: Dict[str, Any]) -> str:
    return _s(trade.get("side") or trade.get("position") or trade.get("direction") or "")


def _is_timeout_exit(exit_reason: str) -> bool:
    r = exit_reason.lower()
    return "timeout" in r or r == "time_exit" or r == "time_limit" or r == "max_hold" or r == "ttl_expired"


def _is_live_candidate(trade: Dict[str, Any]) -> bool:
    # Strict: reject explicit paper/play-money/test.
    unit = _s(trade.get("unit")).lower()
    if unit in ("play_money", "paper", "test"):
        return False
    mode = _s(trade.get("mode")).lower()
    if mode in ("paper", "sim", "simulation", "backtest"):
        return False
    if bool(trade.get("paper_trading")) or bool(trade.get("is_paper")):
        return False
    # If trade includes a definitive "is_live" flag, honor it.
    if trade.get("is_live") is False:
        return False
    return True


def _execution_proven(trade: Dict[str, Any], post_trade_out: Optional[Dict[str, Any]]) -> Optional[bool]:
    # Prefer universal proof flag if present on the trade.
    for k in ("final_execution_proven", "execution_proven", "execution_proven_true"):
        if k in trade:
            v = trade.get(k)
            if isinstance(v, bool):
                return v
    # Some sinks stuff this under post-trade output.
    if isinstance(post_trade_out, dict):
        # We treat missing as unknown.
        v = post_trade_out.get("final_execution_proven")
        if isinstance(v, bool):
            return v
    return None


def _telegram_sent_flags(post_trade_out: Optional[Dict[str, Any]]) -> Tuple[Optional[bool], Optional[bool]]:
    # We only know about the close telegram from the close hub call.
    if not isinstance(post_trade_out, dict):
        return None, None
    tg = post_trade_out.get("telegram")
    if not isinstance(tg, dict):
        return None, None
    close_sent = tg.get("sent")
    return None, bool(close_sent) if isinstance(close_sent, bool) else None


def _supabase_synced_flag(trade: Dict[str, Any], post_trade_out: Optional[Dict[str, Any]]) -> Optional[bool]:
    for k in ("supabase_synced", "supabase_sync_ok", "remote_sync_ok"):
        if isinstance(trade.get(k), bool):
            return bool(trade.get(k))
    if isinstance(post_trade_out, dict):
        # Some subsystems store "execution_intelligence" blobs; if present, never guess.
        v = post_trade_out.get("supabase_synced")
        if isinstance(v, bool):
            return v
    return None


def _rebuy_truth_from_trade(trade: Dict[str, Any], post_trade_out: Optional[Dict[str, Any]]) -> Tuple[Optional[bool], Optional[bool]]:
    # Prefer explicit trade-level rebuy evaluation if it exists.
    attempted = trade.get("rebuy_handoff_attempted")
    allowed = trade.get("rebuy_handoff_allowed")
    if isinstance(attempted, bool) and isinstance(allowed, bool):
        return attempted, allowed
    # Fallback to universal execution loop proof fields when present on the trade payload.
    if isinstance(trade.get("ready_for_rebuy"), bool):
        return True, bool(trade.get("ready_for_rebuy"))
    if isinstance(post_trade_out, dict):
        loop = post_trade_out.get("execution_intelligence") or {}
        if isinstance(loop, dict) and isinstance(loop.get("ready_for_rebuy"), bool):
            return True, bool(loop.get("ready_for_rebuy"))
    return (attempted if isinstance(attempted, bool) else None), (allowed if isinstance(allowed, bool) else None)


def _safe_median(xs: Sequence[float]) -> float:
    if not xs:
        return 0.0
    try:
        return float(median(xs))
    except Exception:
        return 0.0


def _profit_factor(net_by_trade: Sequence[float]) -> Optional[float]:
    gains = sum(x for x in net_by_trade if x > 0)
    losses = -sum(x for x in net_by_trade if x < 0)
    if losses <= 0:
        return None if gains <= 0 else float("inf")
    return gains / losses


def _max_consecutive_losses(net_by_trade: Sequence[float]) -> int:
    best = 0
    cur = 0
    for x in net_by_trade:
        if x < 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def _cumulative_drawdown(net_by_trade: Sequence[float]) -> float:
    eq = 0.0
    peak = 0.0
    max_dd = 0.0
    for x in net_by_trade:
        eq += x
        peak = max(peak, eq)
        dd = peak - eq
        max_dd = max(max_dd, dd)
    return max_dd


def _worst_rolling_k(net_by_trade: Sequence[float], k: int) -> float:
    if not net_by_trade:
        return 0.0
    if k <= 0:
        return 0.0
    if len(net_by_trade) <= k:
        return sum(net_by_trade)
    worst = float("inf")
    cur = sum(net_by_trade[:k])
    worst = min(worst, cur)
    for i in range(k, len(net_by_trade)):
        cur += net_by_trade[i] - net_by_trade[i - k]
        worst = min(worst, cur)
    return worst if worst != float("inf") else 0.0


@dataclass(frozen=True)
class IngestResult:
    status: str
    accepted: bool
    trade_id: str
    window_complete: bool
    report_written: bool
    completion_telegram_text: Optional[str] = None


def default_state() -> Dict[str, Any]:
    return {
        "truth_version": TRUTH_VERSION,
        "window_target": WINDOW_TARGET,
        "closed_trades_count": 0,
        "window_complete": False,
        "ready_for_final_judgment": False,
        "evaluation_mode": "collecting",
        "last_updated_at": "",
        "trade_ids": [],
        "venues_seen": [],
        "gates_seen": [],
        "symbols_seen": [],
        "global_metrics": {},
        "by_gate": {},
        "by_symbol": {},
        "failure_patterns": {},
        "infra_integrity": {},
        "judgment": {},
    }


def _adapter(runtime_root: Optional[Path]) -> LocalStorageAdapter:
    return LocalStorageAdapter(runtime_root=runtime_root)


def _read_json(ad: LocalStorageAdapter, rel: str) -> Optional[Dict[str, Any]]:
    try:
        j = ad.read_json(rel)
        return j if isinstance(j, dict) else None
    except Exception:
        return None


def _write_json(ad: LocalStorageAdapter, rel: str, payload: Dict[str, Any]) -> None:
    ad.write_json(rel, payload)


def _write_text(ad: LocalStorageAdapter, rel: str, text: str) -> None:
    ad.write_text(rel, text)


def ensure_bootstrap(*, runtime_root: Optional[Path]) -> None:
    ad = _adapter(runtime_root)
    if not ad.exists(P_STATE):
        _write_json(ad, P_STATE, default_state())
    if not ad.exists(P_REPORT_JSON):
        _write_json(ad, P_REPORT_JSON, {"status": "empty", "honesty": "Awaiting first live closed trade."})
    if not ad.exists(P_REPORT_TXT):
        _write_text(ad, P_REPORT_TXT, "")


def _is_minimally_closed_trade_valid(trade: Dict[str, Any]) -> Tuple[bool, List[str]]:
    missing: List[str] = []
    if not _s(trade.get("trade_id")):
        missing.append("trade_id")
    close_ts = _extract_timestamp(trade, ("timestamp_close", "closed_at", "exit_time", "timestamp"))
    if not close_ts:
        missing.append("timestamp_close")
    # We do not require economics for minimal validity; those are tracked as missing_fields.
    return (len(missing) == 0), missing


def _extract_economics(trade: Dict[str, Any]) -> Tuple[Optional[float], Optional[float], Optional[float], List[str]]:
    missing: List[str] = []
    gross = None
    fees = None
    net = None
    for k in ("gross_pnl", "gross_pnl_usd", "gross_pnl_dollars", "realized_pnl_dollars", "pnl"):
        if gross is None:
            gross = _sf(trade.get(k))
    for k in ("fees_paid", "fees_total", "execution_fees", "commission_dollars", "total_execution_cost_dollars"):
        if fees is None:
            fees = _sf(trade.get(k))
    for k in ("net_pnl", "net_pnl_usd", "net_pnl_dollars"):
        if net is None:
            net = _sf(trade.get(k))

    if gross is None:
        missing.append("gross_pnl")
    if fees is None:
        missing.append("fees_paid")
    if net is None:
        # Only derive net if both gross and fees exist (never fake).
        if gross is not None and fees is not None:
            net = gross - fees
        else:
            missing.append("net_pnl")
    return gross, fees, net, missing


def _extract_slippage(trade: Dict[str, Any]) -> Tuple[Optional[float], bool]:
    """
    Slippage is optional ("if available").

    Returns (slippage_dollars_if_explicit, slippage_unknown_bool).
    We never coerce bps into dollars without notional.
    """
    # Slippage may be bps or dollars; we only aggregate known numeric dollars if explicitly present.
    for k in ("slippage", "slippage_usd", "slippage_dollars"):
        v = _sf(trade.get(k))
        if v is not None:
            return v, False
    bps_e = _sf(trade.get("entry_slippage_bps"))
    bps_x = _sf(trade.get("exit_slippage_bps"))
    if bps_e is None and bps_x is None:
        return None, True
    # We cannot translate bps into dollars without notional; slippage is not "unknown", it is "known in bps".
    return None, False


def _normalize_trade_record(
    trade: Dict[str, Any],
    post_trade_out: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    tid = _s(trade.get("trade_id"))
    gate_id, venue_id = _infer_gate_and_venue(trade)
    symbol = _infer_symbol(trade)

    ts_open = _extract_timestamp(trade, ("timestamp_open", "opened_at", "entry_time", "timestamp_opened"))
    ts_close = _extract_timestamp(trade, ("timestamp_close", "closed_at", "exit_time", "timestamp"))
    hold_s = _derive_hold_seconds(ts_open, ts_close, trade.get("hold_seconds"))

    entry_price = _sf(trade.get("entry_price"))
    exit_price = _sf(trade.get("exit_price"))
    gross, fees, net, missing_econ = _extract_economics(trade)
    slippage, slippage_unknown = _extract_slippage(trade)

    exit_reason = _exit_reason(trade)
    side = _side(trade)
    req_quote = trade.get("requested_quote") or trade.get("requested_size") or trade.get("requested_notional")
    appr_quote = trade.get("approved_quote") or trade.get("approved_size") or trade.get("approved_notional")

    exec_proven = _execution_proven(trade, post_trade_out)
    supa = _supabase_synced_flag(trade, post_trade_out)
    tg_open, tg_close = _telegram_sent_flags(post_trade_out)
    rebuy_attempted, rebuy_allowed = _rebuy_truth_from_trade(trade, post_trade_out)

    edge_family = trade.get("edge_family")
    strategy_id = trade.get("strategy_id") or trade.get("strategy_key")

    # Missing fields are STRICT economic truth needed for fee-aware validation.
    # Slippage is "if available" and must never be treated as required truth.
    missing_fields = sorted(set(missing_econ))

    return {
        "trade_id": tid,
        "venue_id": venue_id,
        "gate_id": gate_id,
        "symbol": symbol,
        "timestamp_open": ts_open,
        "timestamp_close": ts_close,
        "hold_seconds": hold_s,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "gross_pnl": gross,
        "fees_paid": fees,
        "slippage": slippage,
        "slippage_unknown": bool(slippage_unknown),
        "net_pnl": net,
        "exit_reason": exit_reason,
        "side": side,
        "requested_quote": req_quote,
        "approved_quote": appr_quote,
        "execution_proven": exec_proven,
        "supabase_synced": supa,
        "telegram_open_sent": tg_open,
        "telegram_close_sent": tg_close,
        "rebuy_handoff_attempted": rebuy_attempted,
        "rebuy_handoff_allowed": rebuy_allowed,
        "edge_family": edge_family,
        "strategy_id": strategy_id,
        "missing_fields": missing_fields,
    }


def _strict_required_truth_fields(trade: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    HARDENING: first-20 / trade20 must fail-closed on missing candidate/snapshot/grade/fees/slippage.
    We only check fields that can be carried on a closed-trade record without re-reading snapshots.
    """
    missing: List[str] = []
    # Candidate truth (minimum)
    for k in ("gap_type", "edge_percent", "confidence_score", "liquidity_score"):
        v = trade.get(k)
        if v is None or (isinstance(v, str) and not v.strip()):
            missing.append(k)
    # Execution grade must be explicit
    if not _s(trade.get("execution_grade")):
        missing.append("execution_grade")
    # Real fees + slippage must be captured (no “unknown”)
    if _sf(trade.get("fees_paid")) is None:
        missing.append("fees_paid")
    # Slippage can be dollars OR bps; require at least one concrete measurement
    slip_d = _sf(trade.get("slippage")) or _sf(trade.get("slippage_usd")) or _sf(trade.get("slippage_dollars"))
    bps_e = _sf(trade.get("entry_slippage_bps"))
    bps_x = _sf(trade.get("exit_slippage_bps"))
    if slip_d is None and bps_e is None and bps_x is None:
        missing.append("slippage")
    return (len(missing) == 0), missing


def _compute_global_metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    # Only trades with known net contribute to economic aggregations.
    net_known = [r for r in rows if _sf(r.get("net_pnl")) is not None]
    net = [float(r["net_pnl"]) for r in net_known]

    fees_known = [r for r in rows if _sf(r.get("fees_paid")) is not None]
    fees = [float(r["fees_paid"]) for r in fees_known]

    gross_known = [r for r in rows if _sf(r.get("gross_pnl")) is not None]
    gross = [float(r["gross_pnl"]) for r in gross_known]

    hold_known = [r for r in rows if _sf(r.get("hold_seconds")) is not None]
    holds = [float(r["hold_seconds"]) for r in hold_known]

    wins = sum(1 for x in net if x > 0)
    losses = sum(1 for x in net if x < 0)
    breakeven = sum(1 for x in net if abs(x) <= 1e-12)

    execution_proven_count = sum(1 for r in rows if r.get("execution_proven") is True)
    supa_ok = sum(1 for r in rows if r.get("supabase_synced") is True)
    tg_open_ok = sum(1 for r in rows if r.get("telegram_open_sent") is True)
    tg_close_ok = sum(1 for r in rows if r.get("telegram_close_sent") is True)
    rebuy_allowed = sum(1 for r in rows if r.get("rebuy_handoff_allowed") is True)
    rebuy_blocked = sum(1 for r in rows if r.get("rebuy_handoff_attempted") is True and r.get("rebuy_handoff_allowed") is False)

    timeout_exits = sum(1 for r in rows if _is_timeout_exit(_s(r.get("exit_reason"))))

    fee_flip = 0
    tiny_edge_loss = 0
    full_close_integrity = 0
    for r in rows:
        g = _sf(r.get("gross_pnl"))
        n = _sf(r.get("net_pnl"))
        f = _sf(r.get("fees_paid"))
        miss = r.get("missing_fields") or []
        if g is not None and n is not None and g > 0 and n < 0:
            fee_flip += 1
        if n is not None and -0.01 <= n < 0:
            tiny_edge_loss += 1
        # Full close integrity: minimal validity + economics + execution proof known true/false (not None).
        # Fail-closed: unknown proof counts as not full integrity.
        if not miss and _s(r.get("trade_id")) and _s(r.get("timestamp_close")) and isinstance(r.get("execution_proven"), bool):
            full_close_integrity += 1

    pf = _profit_factor(net)
    gains = sum(x for x in net if x > 0)
    losses_abs = -sum(x for x in net if x < 0)

    payoff_ratio = None
    try:
        avg_win = (sum(x for x in net if x > 0) / wins) if wins else None
        avg_loss = (-sum(x for x in net if x < 0) / losses) if losses else None
        if avg_win is not None and avg_loss and avg_loss > 0:
            payoff_ratio = avg_win / avg_loss
    except Exception:
        payoff_ratio = None

    gross_total = sum(gross) if gross else 0.0
    fees_total = sum(fees) if fees else 0.0
    net_total = sum(net) if net else 0.0

    fee_to_gross_ratio = None
    if gross_total != 0:
        fee_to_gross_ratio = abs(fees_total) / abs(gross_total)

    expectancy = net_total / len(net) if net else 0.0

    # Risk metrics
    max_consec = _max_consecutive_losses(net)
    largest_loss = min(net) if net else 0.0
    dd = _cumulative_drawdown(net)
    worst5 = _worst_rolling_k(net, 5)

    return {
        "count_metrics": {
            "closed_trades": len(rows),
            "winning_trades_net": wins,
            "losing_trades_net": losses,
            "breakeven_trades_net": breakeven,
            "execution_proven_count": execution_proven_count,
            "supabase_sync_success_count": supa_ok,
            "telegram_open_success_count": tg_open_ok,
            "telegram_close_success_count": tg_close_ok,
            "rebuy_allowed_count": rebuy_allowed,
            "rebuy_blocked_count": rebuy_blocked,
        },
        "profitability_metrics": {
            "gross_pnl_total": gross_total,
            "fees_total": fees_total,
            "slippage_total_if_known": None,  # only sum if dollar slippage fields exist (not inferred)
            "net_pnl_total": net_total,
            "avg_net_pnl_per_trade": (net_total / len(net)) if net else 0.0,
            "median_net_pnl_per_trade": _safe_median(net),
            "avg_gross_pnl_per_trade": (gross_total / len(gross)) if gross else 0.0,
            "avg_fees_per_trade": (fees_total / len(fees)) if fees else 0.0,
            "fee_to_gross_ratio": fee_to_gross_ratio,
            "expectancy_net_per_trade": expectancy,
        },
        "quality_metrics": {
            "win_rate_net": (wins / len(net)) if net else 0.0,
            "loss_rate_net": (losses / len(net)) if net else 0.0,
            "payoff_ratio_net": payoff_ratio,
            "profit_factor_net": pf,
            "average_hold_seconds": (sum(holds) / len(holds)) if holds else 0.0,
            "median_hold_seconds": _safe_median(holds),
            "timeout_exit_rate": (timeout_exits / len(rows)) if rows else 0.0,
            "fee_flip_rate": (fee_flip / len(rows)) if rows else 0.0,
            "tiny_edge_loss_rate": (tiny_edge_loss / len(rows)) if rows else 0.0,
            "full_close_integrity_rate": (full_close_integrity / len(rows)) if rows else 0.0,
            "known_net_rate": (len(net) / len(rows)) if rows else 0.0,
        },
        "risk_metrics": {
            "max_consecutive_net_losses": max_consec,
            "largest_single_net_loss": float(largest_loss) if net else 0.0,
            "cumulative_drawdown_over_window": float(dd),
            "worst_5_trade_rolling_net": float(worst5),
            "risk_breaker_trigger_count": None,
            "reduced_mode_count": None,
            "paused_mode_count": None,
        },
        "supporting_totals": {
            "net_known_trade_count": len(net),
            "fees_known_trade_count": len(fees),
            "gross_known_trade_count": len(gross),
            "net_gains_total": gains,
            "net_losses_abs_total": losses_abs,
        },
    }


def _compute_by_key(rows: List[Dict[str, Any]], key: str) -> Dict[str, Any]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        k = _s(r.get(key)) or f"unknown_{key}"
        groups.setdefault(k, []).append(r)

    out: Dict[str, Any] = {}
    for k, rs in groups.items():
        net = [float(x["net_pnl"]) for x in rs if _sf(x.get("net_pnl")) is not None]
        fees = [float(x["fees_paid"]) for x in rs if _sf(x.get("fees_paid")) is not None]

        wins = sum(1 for x in net if x > 0)
        pf = _profit_factor(net)
        timeout_rate = sum(1 for r in rs if _is_timeout_exit(_s(r.get("exit_reason")))) / len(rs) if rs else 0.0
        fee_flip_rate = (
            sum(
                1
                for r in rs
                if (_sf(r.get("gross_pnl")) is not None and _sf(r.get("net_pnl")) is not None)
                and float(r["gross_pnl"]) > 0
                and float(r["net_pnl"]) < 0
            )
            / len(rs)
            if rs
            else 0.0
        )
        largest_loss = min(net) if net else 0.0
        max_consec = _max_consecutive_losses(net)

        # Status rules
        if len(rs) < 3:
            status = "insufficient_sample"
        else:
            net_total = sum(net) if net else 0.0
            if net_total < 0 and (pf is not None and pf < 1):
                status = "bleeding"
            elif net_total > 0 and (pf is None or pf >= 1):
                status = "healthy"
            else:
                status = "mixed"

        tags: List[str] = []
        if sum(fees) > 0 and sum(net) <= 0 and fee_flip_rate > 0:
            tags.append("fee_dominant")
        if timeout_rate >= 0.25:
            tags.append("timeout_dominant")
        if status == "bleeding":
            tags.append("low_edge_after_costs")

        out[k] = {
            "trade_count": len(rs),
            "net_pnl_total": sum(net) if net else 0.0,
            "fees_total": sum(fees) if fees else 0.0,
            "win_rate_net": (wins / len(net)) if net else 0.0,
            "profit_factor_net": pf,
            "avg_net_per_trade": (sum(net) / len(net)) if net else 0.0,
            "timeout_exit_rate": timeout_rate,
            "fee_flip_rate": fee_flip_rate,
            "largest_net_loss": float(largest_loss) if net else 0.0,
            "max_consecutive_losses": max_consec,
            "status": status,
            "primary_problem_tags": tags,
        }
    return out


def _detect_failure_patterns(rows: List[Dict[str, Any]], by_gate: Dict[str, Any]) -> Dict[str, Any]:
    trade_ids = [str(r.get("trade_id") or "") for r in rows]

    fee_flip_ids: List[str] = []
    gross_pos_net_neg = 0
    fees_total = 0.0
    gross_total = 0.0
    for r in rows:
        g = _sf(r.get("gross_pnl"))
        n = _sf(r.get("net_pnl"))
        f = _sf(r.get("fees_paid"))
        if f is not None:
            fees_total += f
        if g is not None:
            gross_total += g
        if g is not None and n is not None and g > 0 and n < 0:
            gross_pos_net_neg += 1
            tid = _s(r.get("trade_id"))
            if tid:
                fee_flip_ids.append(tid)

    fee_dom_active = False
    if gross_total != 0:
        ratio = abs(fees_total) / max(1e-9, abs(gross_total))
        fee_dom_active = ratio >= 0.35 or gross_pos_net_neg >= 3
    else:
        fee_dom_active = gross_pos_net_neg >= 3

    timeout_bad: List[str] = []
    timeout_total = 0
    for r in rows:
        if _is_timeout_exit(_s(r.get("exit_reason"))):
            timeout_total += 1
            n = _sf(r.get("net_pnl"))
            if n is not None and n < 0:
                tid = _s(r.get("trade_id"))
                if tid:
                    timeout_bad.append(tid)
    timeout_active = timeout_total >= max(3, int(0.25 * max(1, len(rows))))

    exec_bad: List[str] = []
    supa_bad: List[str] = []
    tg_bad: List[str] = []
    missing_truth: List[str] = []
    for r in rows:
        tid = _s(r.get("trade_id"))
        if r.get("execution_proven") is not True:
            if tid:
                exec_bad.append(tid)
        if r.get("supabase_synced") is False:
            if tid:
                supa_bad.append(tid)
        if r.get("telegram_close_sent") is False:
            if tid:
                tg_bad.append(tid)
        miss = r.get("missing_fields") or []
        if miss:
            if tid:
                missing_truth.append(tid)
    exec_active = len(exec_bad) >= 2 or len(supa_bad) >= 2 or len(tg_bad) >= 2

    # Rebuy quality: fail-closed — unknown counts as not clean, but "unsafe" only when explicit.
    unsafe_rebuy_ids: List[str] = []
    aggressive_allowed_after_loss = 0
    attempts = 0
    allowed = 0
    for r in rows:
        if r.get("rebuy_handoff_attempted") is True:
            attempts += 1
        if r.get("rebuy_handoff_allowed") is True:
            allowed += 1
            n = _sf(r.get("net_pnl"))
            if n is not None and n < 0:
                aggressive_allowed_after_loss += 1
                tid = _s(r.get("trade_id"))
                if tid:
                    unsafe_rebuy_ids.append(tid)
    rebuy_active = aggressive_allowed_after_loss >= 2 or (attempts >= 3 and allowed / max(1, attempts) >= 0.8)

    # Gate bleed: identify single clear worst gate by net.
    gate_bleed_ids: List[str] = []
    gate_bleed_active = False
    worst_gate = None
    worst_net = 0.0
    if by_gate:
        for gid, row in by_gate.items():
            net = _sf(row.get("net_pnl_total")) or 0.0
            if worst_gate is None or net < worst_net:
                worst_gate = gid
                worst_net = net
        if worst_gate is not None and worst_net < 0:
            # Clear source if worst is <0 and at least 60% of total losses (when computable).
            total_net = sum((_sf(v.get("net_pnl_total")) or 0.0) for v in by_gate.values())
            if total_net < 0 and abs(worst_net) >= 0.6 * abs(total_net):
                gate_bleed_active = True
                for r in rows:
                    if _s(r.get("gate_id")) == str(worst_gate):
                        tid = _s(r.get("trade_id"))
                        if tid:
                            gate_bleed_ids.append(tid)

    return {
        "fee_dominance_cluster": {
            "active": bool(fee_dom_active),
            "supporting_trade_ids": _as_list_unique(fee_flip_ids)[:20],
            "explanation": (
                "Fees materially consumed edge; repeated fee flips (gross>0 but net<0) detected."
                if fee_dom_active
                else "No material fee-dominance pattern detected."
            ),
            "recommended_action": (
                "Increase min net profit floor and/or raise edge threshold; tighten fee-aware preflight."
                if fee_dom_active
                else "No action."
            ),
        },
        "timeout_loss_cluster": {
            "active": bool(timeout_active),
            "supporting_trade_ids": _as_list_unique(timeout_bad)[:20],
            "explanation": (
                "Timeout exits are common and often negative net."
                if timeout_active
                else "Timeout exits not dominant."
            ),
            "recommended_action": (
                "Tighten timeout policy, reduce holds, or adjust exit logic for timeout conditions."
                if timeout_active
                else "No action."
            ),
        },
        "execution_integrity_cluster": {
            "active": bool(exec_active),
            "supporting_trade_ids": _as_list_unique(exec_bad + supa_bad + tg_bad + missing_truth)[:30],
            "explanation": (
                "Execution proof / sync / telegram or truth fields are missing/inconsistent across multiple trades."
                if exec_active
                else "No multi-trade integrity cluster detected."
            ),
            "recommended_action": (
                "Pause until proof+sync pipeline is clean; increase proof requirement and fix artifact mismatches."
                if exec_active
                else "No action."
            ),
        },
        "rebuy_quality_cluster": {
            "active": bool(rebuy_active),
            "supporting_trade_ids": _as_list_unique(unsafe_rebuy_ids)[:20],
            "explanation": (
                "Rebuy allowed too aggressively after losing closes / weak conditions."
                if rebuy_active
                else "No unsafe rebuy cluster detected."
            ),
            "recommended_action": (
                "Tighten rebuy rule; require stronger proof/edge after loss before allowing rebuy."
                if rebuy_active
                else "No action."
            ),
        },
        "gate_bleed_cluster": {
            "active": bool(gate_bleed_active),
            "supporting_trade_ids": _as_list_unique(gate_bleed_ids)[:20],
            "explanation": (
                f"Gate {worst_gate} is the clear source of losses in the window."
                if gate_bleed_active
                else "No single gate dominates losses."
            ),
            "recommended_action": (
                f"Pause or reduce gate {worst_gate}; tighten scope until gate metrics recover."
                if gate_bleed_active
                else "No action."
            ),
        },
        "meta": {"window_trade_ids": _as_list_unique(trade_ids)[:WINDOW_TARGET]},
    }


def _compute_infra_integrity(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(rows)
    if n <= 0:
        return {}

    def tri(pass_count: int, warn_threshold: float, fail_threshold: float) -> str:
        rate = pass_count / n
        if rate >= warn_threshold:
            return "PASS"
        if rate >= fail_threshold:
            return "WARN"
        return "FAIL"

    exec_pass = sum(1 for r in rows if r.get("execution_proven") is True)
    # Databank integrity is not fully observable from post_trade_out; treat missing as WARN not FAIL.
    databank_pass = sum(1 for r in rows if "databank" in _s(r.get("venue_id")).lower())  # placeholder signal
    supa_pass = sum(1 for r in rows if r.get("supabase_synced") is True)
    tg_close_pass = sum(1 for r in rows if r.get("telegram_close_sent") is True)
    truth_artifact_pass = sum(1 for r in rows if not (r.get("missing_fields") or []))
    rebuy_truth_pass = sum(
        1
        for r in rows
        if (r.get("rebuy_handoff_attempted") is None and r.get("rebuy_handoff_allowed") is None)
        or isinstance(r.get("rebuy_handoff_allowed"), bool)
    )

    exec_ids = [r["trade_id"] for r in rows if r.get("execution_proven") is not True and _s(r.get("trade_id"))]
    supa_ids = [r["trade_id"] for r in rows if r.get("supabase_synced") is False and _s(r.get("trade_id"))]
    tg_ids = [r["trade_id"] for r in rows if r.get("telegram_close_sent") is False and _s(r.get("trade_id"))]
    truth_ids = [r["trade_id"] for r in rows if (r.get("missing_fields") or []) and _s(r.get("trade_id"))]

    return {
        "execution_proof_integrity": {
            "status": tri(exec_pass, 1.0, 0.8),
            "pass_count": exec_pass,
            "total": n,
            "evidence_trade_ids": _as_list_unique(exec_ids)[:30],
        },
        "databank_integrity": {
            "status": "WARN",
            "honesty": "Databank append/readback not fully observable from this hook without explicit fields.",
        },
        "supabase_integrity": {
            "status": tri(supa_pass, 0.95, 0.8) if any(r.get("supabase_synced") is not None for r in rows) else "WARN",
            "pass_count": supa_pass,
            "known_count": sum(1 for r in rows if r.get("supabase_synced") is not None),
            "evidence_trade_ids": _as_list_unique(supa_ids)[:30],
        },
        "telegram_integrity": {
            "status": tri(tg_close_pass, 0.95, 0.8) if any(r.get("telegram_close_sent") is not None for r in rows) else "WARN",
            "pass_count": tg_close_pass,
            "known_count": sum(1 for r in rows if r.get("telegram_close_sent") is not None),
            "evidence_trade_ids": _as_list_unique(tg_ids)[:30],
        },
        "truth_artifact_integrity": {
            "status": tri(truth_artifact_pass, 1.0, 0.8),
            "pass_count": truth_artifact_pass,
            "total": n,
            "evidence_trade_ids": _as_list_unique(truth_ids)[:30],
        },
        "rebuy_truth_integrity": {
            "status": tri(rebuy_truth_pass, 0.95, 0.8),
            "pass_count": rebuy_truth_pass,
            "total": n,
        },
    }


def _final_judgment(
    *,
    window_complete: bool,
    global_metrics: Dict[str, Any],
    infra: Dict[str, Any],
    failure_patterns: Dict[str, Any],
    by_gate: Dict[str, Any],
) -> Dict[str, Any]:
    gm_p = global_metrics.get("profitability_metrics") or {}
    gm_q = global_metrics.get("quality_metrics") or {}
    gm_r = global_metrics.get("risk_metrics") or {}

    net_total = _sf(gm_p.get("net_pnl_total")) or 0.0
    pf = gm_q.get("profit_factor_net")
    if pf == float("inf"):
        pf_num = 999.0
    else:
        pf_num = _sf(pf)

    # Economic result
    if net_total > 0:
        economic = "profitable"
    elif abs(net_total) <= 1e-9:
        economic = "flat"
    else:
        economic = "unprofitable"

    # Execution result: derived from infra + integrity cluster.
    exec_status = (infra.get("execution_proof_integrity") or {}).get("status") or "WARN"
    truth_status = (infra.get("truth_artifact_integrity") or {}).get("status") or "WARN"
    exec_cluster = bool((failure_patterns.get("execution_integrity_cluster") or {}).get("active"))
    if exec_status == "PASS" and truth_status == "PASS" and not exec_cluster:
        execution_result = "clean"
    elif exec_status == "FAIL" or truth_status == "FAIL" or exec_cluster:
        execution_result = "unsafe"
    else:
        execution_result = "mixed"

    # Risk result
    max_consec = _si(gm_r.get("max_consecutive_net_losses")) or 0
    dd = _sf(gm_r.get("cumulative_drawdown_over_window")) or 0.0
    worst5 = _sf(gm_r.get("worst_5_trade_rolling_net")) or 0.0
    if max_consec >= 6 or dd >= 0.0 and dd > 0 and worst5 < -abs(net_total) * 1.2:
        risk_result = "uncontrolled"
    elif max_consec >= 4 or dd > 0 and dd >= max(50.0, abs(net_total) * 0.8):
        risk_result = "warning"
    else:
        risk_result = "controlled"

    # Gate result
    bleeding_gates = [g for g, r in by_gate.items() if str(r.get("status")) == "bleeding"]
    if not bleeding_gates:
        gate_result = "healthy"
    elif len(bleeding_gates) == 1:
        gate_result = "one_gate_bleeding"
    else:
        gate_result = "multi_gate_bleeding"

    # Rebuy result
    rebuy_cluster = bool((failure_patterns.get("rebuy_quality_cluster") or {}).get("active"))
    rebuy_result = "unsafe" if rebuy_cluster else "safe"

    # Infra result
    infra_statuses = [
        (infra.get("execution_proof_integrity") or {}).get("status"),
        (infra.get("supabase_integrity") or {}).get("status"),
        (infra.get("telegram_integrity") or {}).get("status"),
        (infra.get("truth_artifact_integrity") or {}).get("status"),
    ]
    if any(s == "FAIL" for s in infra_statuses if s):
        infra_result = "broken"
    elif any(s == "WARN" for s in infra_statuses if s):
        infra_result = "mixed"
    else:
        infra_result = "clean"

    blockers: List[str] = []
    next_actions: List[str] = []

    if not window_complete:
        blockers.append("window_not_complete")
        next_actions.append("Continue collecting closed live trades until window_target=20 is reached.")
    if net_total <= 0:
        blockers.append("net_pnl_total_not_positive")
        next_actions.append("Raise edge threshold / tighten scope until net after fees turns positive.")
    if pf_num is None or pf_num <= 1.0:
        blockers.append("profit_factor_net_not_above_1")
        next_actions.append("Reduce bleeding gates/symbols; raise net profit floor; improve exits.")
    if infra_result == "broken":
        blockers.append("infra_broken")
        next_actions.append("Fix execution proof / Supabase / Telegram integrity before continuing.")
    if risk_result == "uncontrolled":
        blockers.append("risk_uncontrolled")
        next_actions.append("Reduce sizing and tighten loss controls; investigate consecutive loss causes.")
    if rebuy_result == "unsafe":
        blockers.append("rebuy_unsafe")
        next_actions.append("Tighten rebuy policy; require stronger proof/edge gates before allowing rebuy.")
    if gate_result in ("one_gate_bleeding", "multi_gate_bleeding"):
        blockers.append("gate_bleeding_unmitigated")
        next_actions.append("Pause/reduce bleeding gate(s); cool down worst symbols; re-run validation window.")
    if bool((failure_patterns.get("fee_dominance_cluster") or {}).get("active")):
        next_actions.append("Fee dominance detected: increase min net profit floor and fee-aware preflight.")

    # Overall decision
    ready_live = (
        window_complete
        and net_total > 0
        and (pf_num is not None and pf_num > 1.0)
        and infra_result != "broken"
        and risk_result != "uncontrolled"
        and rebuy_result != "unsafe"
        and gate_result == "healthy"
    )
    if ready_live:
        overall = "READY_LIVE"
    else:
        # Reduced if there is promise (not broken infra, not deeply negative) but warnings remain.
        if infra_result != "broken" and economic != "unprofitable" and execution_result != "unsafe":
            overall = "READY_REDUCED"
        else:
            overall = "PAUSE_AND_FIX"

    return {
        "window_complete": bool(window_complete),
        "economic_result": economic,
        "execution_result": execution_result,
        "risk_result": risk_result,
        "gate_result": gate_result,
        "rebuy_result": rebuy_result,
        "infra_result": infra_result,
        "overall_result": overall,
        "exact_blockers": _as_list_unique(blockers),
        "exact_next_actions": _as_list_unique(next_actions),
    }


def _gate_action_recommendations(by_gate: Dict[str, Any], by_symbol: Dict[str, Any]) -> Dict[str, Any]:
    gates: Dict[str, Any] = {}
    for gid, row in by_gate.items():
        status = str(row.get("status") or "")
        if status == "healthy":
            action = "continue_gate"
        elif status == "bleeding":
            action = "pause_gate"
        elif status == "mixed":
            action = "reduce_gate"
        else:
            action = "reduce_gate"
        recs: List[str] = []
        tags = row.get("primary_problem_tags") or []
        if "fee_dominant" in tags:
            recs.append("increase_min_net_profit_floor")
            recs.append("increase_edge_threshold")
        if "timeout_dominant" in tags:
            recs.append("tighten_timeout_policy")
        if "low_edge_after_costs" in tags:
            recs.append("increase_edge_threshold")
        gates[gid] = {"recommended_action": action, "recommended_tunings": _as_list_unique(recs)}

    symbols: Dict[str, Any] = {}
    # Cool down / pause worst symbols based on status.
    for sym, row in by_symbol.items():
        status = str(row.get("status") or "")
        if status == "healthy":
            act = "continue_symbol"
        elif status == "bleeding":
            act = "pause_symbol"
        elif status == "mixed":
            act = "cool_down_symbol"
        else:
            act = "cool_down_symbol"
        symbols[sym] = {"recommended_action": act}

    return {"by_gate": gates, "by_symbol": symbols, "schema": "trade20_gate_actions_v1"}


def _lessons_artifact(report: Dict[str, Any]) -> Dict[str, Any]:
    fp = report.get("failure_patterns") or {}
    lessons: List[Dict[str, Any]] = []
    for name in (
        "fee_dominance_cluster",
        "timeout_loss_cluster",
        "execution_integrity_cluster",
        "rebuy_quality_cluster",
        "gate_bleed_cluster",
    ):
        c = fp.get(name) or {}
        if c.get("active"):
            lessons.append(
                {
                    "pattern": name,
                    "supporting_trade_ids": c.get("supporting_trade_ids") or [],
                    "lesson": c.get("explanation") or "",
                    "recommended_action": c.get("recommended_action") or "",
                }
            )
    return {
        "truth_version": TRUTH_VERSION,
        "generated_at": _now_iso(),
        "overall_result": (report.get("judgment") or {}).get("overall_result"),
        "lessons": lessons,
        "honesty": "Lessons are derived strictly from the first 20 closed live trades recorded into this validator.",
    }


def _ceo_review_input(report: Dict[str, Any]) -> Dict[str, Any]:
    gm = report.get("global_metrics") or {}
    by_gate = report.get("by_gate") or {}
    by_symbol = report.get("by_symbol") or {}
    judgment = report.get("judgment") or {}
    return {
        "truth_version": TRUTH_VERSION,
        "generated_at": _now_iso(),
        "window": {
            "target": WINDOW_TARGET,
            "closed_trades_count": int(report.get("closed_trades_count") or 0),
            "trade_ids": report.get("trade_ids") or [],
        },
        "headline": {
            "overall_result": judgment.get("overall_result"),
            "economic_result": judgment.get("economic_result"),
            "execution_result": judgment.get("execution_result"),
            "risk_result": judgment.get("risk_result"),
            "gate_result": judgment.get("gate_result"),
            "infra_result": judgment.get("infra_result"),
        },
        "global_metrics": gm,
        "by_gate": by_gate,
        "by_symbol": by_symbol,
        "failure_patterns": report.get("failure_patterns") or {},
        "infra_integrity": report.get("infra_integrity") or {},
        "blockers": judgment.get("exact_blockers") or [],
        "next_actions": judgment.get("exact_next_actions") or [],
        "honesty": "CEO input is computed from validator state; no backtest inference.",
    }


def _report_to_txt(report: Dict[str, Any]) -> str:
    j = report.get("judgment") or {}
    gm = report.get("global_metrics") or {}
    pm = (gm.get("profitability_metrics") or {}) if isinstance(gm, dict) else {}
    qm = (gm.get("quality_metrics") or {}) if isinstance(gm, dict) else {}
    fees = pm.get("fees_total")
    net = pm.get("net_pnl_total")
    pf = qm.get("profit_factor_net")
    lines = [
        "TRADE20 VALIDATION REPORT",
        "=========================",
        f"truth_version: {report.get('truth_version')}",
        f"window_target: {report.get('window_target')}",
        f"closed_trades_count: {report.get('closed_trades_count')}",
        f"window_complete: {report.get('window_complete')}",
        "",
        f"net_pnl_total: {net}",
        f"fees_total: {fees}",
        f"profit_factor_net: {pf}",
        "",
        f"overall_result: {j.get('overall_result')}",
        f"economic_result: {j.get('economic_result')}",
        f"execution_result: {j.get('execution_result')}",
        f"risk_result: {j.get('risk_result')}",
        f"gate_result: {j.get('gate_result')}",
        f"rebuy_result: {j.get('rebuy_result')}",
        f"infra_result: {j.get('infra_result')}",
        "",
        "exact_blockers:",
        *[f"- {b}" for b in (j.get("exact_blockers") or [])],
        "",
        "exact_next_actions:",
        *[f"- {a}" for a in (j.get("exact_next_actions") or [])],
        "",
    ]
    return "\n".join(lines).strip() + "\n"


def build_completion_telegram_summary(report: Dict[str, Any]) -> str:
    gm = report.get("global_metrics") or {}
    pm = gm.get("profitability_metrics") or {}
    net = pm.get("net_pnl_total")
    fees = pm.get("fees_total")
    by_gate = report.get("by_gate") or {}
    best_gate = None
    worst_gate = None
    best_net = None
    worst_net = None
    for gid, row in by_gate.items():
        n = _sf(row.get("net_pnl_total"))
        if n is None:
            continue
        if best_gate is None or (best_net is not None and n > best_net):
            best_gate, best_net = gid, n
        if worst_gate is None or (worst_net is not None and n < worst_net):
            worst_gate, worst_net = gid, n
    j = report.get("judgment") or {}
    blockers = (j.get("exact_blockers") or [])[:3]
    lines = [
        "Ezras — TRADE20 WINDOW COMPLETE",
        "",
        f"Closed trades: {report.get('closed_trades_count')}/{WINDOW_TARGET}",
        f"Net PnL (after fees): {net}",
        f"Fees total: {fees}",
        f"Best gate: {best_gate or '—'}",
        f"Worst gate: {worst_gate or '—'}",
        f"Overall: {j.get('overall_result')}",
    ]
    if j.get("overall_result") != "READY_LIVE" and blockers:
        lines.extend(["", "Top blockers:"] + [f"- {b}" for b in blockers])
    return "\n".join(lines).strip() + "\n"


def _write_completion_outputs(ad: LocalStorageAdapter, report: Dict[str, Any]) -> None:
    _write_json(ad, P_REVIEW_INPUT, _ceo_review_input(report))
    _write_json(ad, P_LESSONS, _lessons_artifact(report))
    _write_json(ad, P_GATE_ACTIONS, _gate_action_recommendations(report.get("by_gate") or {}, report.get("by_symbol") or {}))


def _compute_full_report(state: Dict[str, Any], rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_gate = _compute_by_key(rows, "gate_id")
    by_symbol = _compute_by_key(rows, "symbol")
    global_metrics = _compute_global_metrics(rows)
    failure_patterns = _detect_failure_patterns(rows, by_gate)
    infra = _compute_infra_integrity(rows)
    judgment = _final_judgment(
        window_complete=bool(state.get("window_complete")),
        global_metrics=global_metrics,
        infra=infra,
        failure_patterns=failure_patterns,
        by_gate=by_gate,
    )
    return {
        "truth_version": TRUTH_VERSION,
        "window_target": WINDOW_TARGET,
        "closed_trades_count": int(state.get("closed_trades_count") or 0),
        "window_complete": bool(state.get("window_complete")),
        "ready_for_final_judgment": bool(state.get("ready_for_final_judgment")),
        "evaluation_mode": str(state.get("evaluation_mode") or "collecting"),
        "last_updated_at": state.get("last_updated_at") or "",
        "trade_ids": state.get("trade_ids") or [],
        "venues_seen": state.get("venues_seen") or [],
        "gates_seen": state.get("gates_seen") or [],
        "symbols_seen": state.get("symbols_seen") or [],
        "global_metrics": global_metrics,
        "by_gate": by_gate,
        "by_symbol": by_symbol,
        "failure_patterns": failure_patterns,
        "infra_integrity": infra,
        "judgment": judgment,
    }


def maybe_process_trade20_closed_trade(
    trade: Dict[str, Any],
    post_trade_out: Optional[Dict[str, Any]] = None,
    *,
    runtime_root: Optional[Path] = None,
    settings: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Integration entry point. Never raises.

    - Updates `trade20_validation_state.json` after every accepted closed live trade.
    - Writes report JSON/TXT every update.
    - When window completes (20 accepted), writes CEO/lessons/gate-actions outputs and optionally sends Telegram.
    """
    try:
        ensure_bootstrap(runtime_root=runtime_root)
        ad = _adapter(runtime_root)
        state = _read_json(ad, P_STATE) or default_state()

        ok_min, missing_min = _is_minimally_closed_trade_valid(trade)
        tid = _s(trade.get("trade_id"))
        if not ok_min:
            # Integrity-only; do not mutate window.
            state.setdefault("infra_integrity", {}).setdefault("integrity_only_rejects", []).append(
                {"trade_id": tid or None, "missing": missing_min, "at": _now_iso()}
            )
            state["last_updated_at"] = _now_iso()
            _write_json(ad, P_STATE, state)
            return {"status": "rejected_integrity_only", "reason": "min_close_invalid", "missing": missing_min}

        if not _is_live_candidate(trade):
            state.setdefault("infra_integrity", {}).setdefault("integrity_only_rejects", []).append(
                {"trade_id": tid, "reason": "not_live_candidate", "at": _now_iso()}
            )
            state["last_updated_at"] = _now_iso()
            _write_json(ad, P_STATE, state)
            return {"status": "rejected_integrity_only", "reason": "not_live_candidate", "trade_id": tid}

        ok_strict, miss_strict = _strict_required_truth_fields(trade)
        if not ok_strict:
            # HARD FAIL for readiness: do not accept into window.
            state.setdefault("infra_integrity", {}).setdefault("strict_rejects", []).append(
                {"trade_id": tid, "missing": miss_strict, "at": _now_iso()}
            )
            state["last_updated_at"] = _now_iso()
            _write_json(ad, P_STATE, state)
            return {"status": "rejected_strict", "reason": "missing_strict_truth", "missing": miss_strict, "trade_id": tid}

        # Dedupe
        trade_ids: List[str] = list(state.get("trade_ids") or [])
        if tid in trade_ids:
            state["last_updated_at"] = _now_iso()
            _write_json(ad, P_STATE, state)
            # Still refresh report (can change integrity inference if surrounding context changed).
            rows = [r for r in (state.get("_rows") or []) if isinstance(r, dict)]  # internal only if present
            report_written = False
            if rows:
                report = _compute_full_report(state, rows)
                _write_json(ad, P_REPORT_JSON, report)
                _write_text(ad, P_REPORT_TXT, _report_to_txt(report))
                report_written = True
            return {"status": "skipped_duplicate", "trade_id": tid, "report_written": report_written}

        # Build row and append into state-local rows.
        row = _normalize_trade_record(trade, post_trade_out)

        # Keep rows in state for deterministic report generation without re-reading upstream.
        rows: List[Dict[str, Any]] = [r for r in (state.get("_rows") or []) if isinstance(r, dict)]
        rows.append(row)

        # Update state contract fields.
        trade_ids.append(tid)
        gate_id = _s(row.get("gate_id"))
        venue_id = _s(row.get("venue_id"))
        symbol = _s(row.get("symbol"))

        state["trade_ids"] = trade_ids[:WINDOW_TARGET]
        state["venues_seen"] = _as_list_unique(list(state.get("venues_seen") or []) + [venue_id])[:50]
        state["gates_seen"] = _as_list_unique(list(state.get("gates_seen") or []) + [gate_id])[:50]
        state["symbols_seen"] = _as_list_unique(list(state.get("symbols_seen") or []) + [symbol])[:200]

        closed = len(state["trade_ids"])
        state["closed_trades_count"] = closed
        state["window_complete"] = closed >= WINDOW_TARGET
        state["ready_for_final_judgment"] = bool(state["window_complete"])
        state["evaluation_mode"] = "complete" if state["window_complete"] else "collecting"
        state["last_updated_at"] = _now_iso()

        # Compute and persist report every trade (evidence-first, no hidden state).
        report = _compute_full_report(state, rows[:WINDOW_TARGET])

        # Store computed sections back into state (without duplicating full report everywhere).
        state["global_metrics"] = report.get("global_metrics") or {}
        state["by_gate"] = report.get("by_gate") or {}
        state["by_symbol"] = report.get("by_symbol") or {}
        state["failure_patterns"] = report.get("failure_patterns") or {}
        state["infra_integrity"] = report.get("infra_integrity") or {}
        state["judgment"] = report.get("judgment") or {}

        # Internal only: keep rows for deterministic recompute; not part of required contract, but tolerated.
        state["_rows"] = rows[:WINDOW_TARGET]

        _write_json(ad, P_STATE, state)
        _write_json(ad, P_REPORT_JSON, report)
        _write_text(ad, P_REPORT_TXT, _report_to_txt(report))

        completion_text: Optional[str] = None
        completion_written = False
        completion_sent = False

        if state["window_complete"]:
            _write_completion_outputs(ad, report)
            completion_written = True
            completion_text = build_completion_telegram_summary(report)
            # Optional telegram hook: only on completion; never raises; idempotent.
            if settings is not None:
                try:
                    from trading_ai.automation.telegram_ops import send_telegram_with_idempotency

                    tg = send_telegram_with_idempotency(
                        settings,
                        completion_text,
                        dedupe_key="trade20:window_complete",
                        event_label="trade20_validation_complete",
                    )
                    completion_sent = bool(tg.get("sent"))
                except Exception:
                    completion_sent = False

        return {
            "status": "accepted",
            "trade_id": tid,
            "closed_trades_count": state["closed_trades_count"],
            "window_complete": state["window_complete"],
            "report_written": True,
            "completion_outputs_written": completion_written,
            "completion_telegram_sent": completion_sent,
            "completion_telegram_text": completion_text if completion_written else None,
            "overall_result": (report.get("judgment") or {}).get("overall_result"),
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def load_trade20_state(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    ensure_bootstrap(runtime_root=runtime_root)
    ad = _adapter(runtime_root)
    return _read_json(ad, P_STATE) or default_state()


def load_trade20_report(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    ensure_bootstrap(runtime_root=runtime_root)
    ad = _adapter(runtime_root)
    j = _read_json(ad, P_REPORT_JSON)
    return j if isinstance(j, dict) else {"status": "empty"}

