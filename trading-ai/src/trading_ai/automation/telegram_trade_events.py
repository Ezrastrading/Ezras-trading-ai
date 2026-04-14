"""
Trade placed / closed → Telegram (non-blocking hooks from Phase 2 trade_ops).

Message copy is formatted for a concise operator / lock-screen feed; transport + idempotency unchanged.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.phase2.config_phase2 import DEFAULT_PHASE2_RISK

logger = logging.getLogger(__name__)


def _nonempty_str(x: Any) -> Optional[str]:
    if x is None:
        return None
    s = str(x).strip()
    return s or None


def _fmt_dollars(x: Any) -> Optional[str]:
    if x is None:
        return None
    try:
        v = float(x)
        return f"${v:,.2f}"
    except (TypeError, ValueError):
        return None


def _fmt_percent(x: Any, *, decimals: int = 1) -> Optional[str]:
    if x is None:
        return None
    try:
        v = float(x)
        return f"{v:.{decimals}f}%"
    except (TypeError, ValueError):
        return None


def _fmt_entry_exit_price(x: Any) -> Optional[str]:
    if x is None:
        return None
    try:
        v = float(x)
        if 0 <= abs(v) <= 1.0:
            s = f"{v:.4f}".rstrip("0").rstrip(".")
            return s or "0"
        return f"{v:,.2f}"
    except (TypeError, ValueError):
        return None


def _format_timestamp(ts: Any) -> str:
    if ts is None:
        return "—"
    raw = str(ts).strip()
    if not raw:
        return "—"
    try:
        s = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return raw[:32]


def _infer_side(trade: Dict[str, Any]) -> str:
    explicit = _nonempty_str(trade.get("side"))
    if explicit:
        return explicit.upper() if explicit.upper().startswith("BUY_") else explicit
    pos = str(trade.get("position") or "").strip().upper()
    if pos == "YES":
        return "BUY_YES"
    if pos == "NO":
        return "BUY_NO"
    return pos or "—"


def _ticker_line(trade: Dict[str, Any]) -> Optional[str]:
    t = _nonempty_str(trade.get("ticker"))
    if t:
        return t
    return _nonempty_str(trade.get("event_name"))


def _strategy_line(trade: Dict[str, Any]) -> Optional[str]:
    parts = [
        trade.get("market_category"),
        trade.get("thematic_cluster_id"),
    ]
    xs = [str(p).strip() for p in parts if p and str(p).strip()]
    if xs:
        return " / ".join(xs[:3])
    return None


def _risk_percent_of_account(trade: Dict[str, Any]) -> Optional[float]:
    for key in ("risk_percent_of_account", "allocated_pct_of_account", "size_percent_of_account"):
        v = trade.get(key)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    ov = trade.get("operator_size_override_percent")
    if ov is not None:
        try:
            return float(ov) * 100.0
        except (TypeError, ValueError):
            pass
    cap = trade.get("capital_allocated")
    eq = trade.get("portfolio_equity") or trade.get("account_equity") or trade.get("account_balance")
    if cap is not None and eq is not None:
        try:
            c, e = float(cap), float(eq)
            if e > 0:
                return (c / e) * 100.0
        except (TypeError, ValueError):
            pass
    return float(DEFAULT_PHASE2_RISK.max_pct_per_trade) * 100.0


def _open_risk_percent_truthful(
    trade: Dict[str, Any],
    *,
    approved_notional: Optional[float],
    requested_notional: Optional[float],
    effective_bucket: str,
    approval_status: Optional[str],
) -> Optional[float]:
    """
    Risk % for OPEN alerts uses **approved** notional vs account equity when available.
    BLOCKED / zero approved -> 0.0%. No equity -> scale max per-trade % by approved/requested.
    """
    eff = str(effective_bucket or "").upper()
    st = str(approval_status or "").upper()
    if eff == "BLOCKED" or st == "BLOCKED":
        return 0.0
    if approved_notional is None:
        return None
    if approved_notional <= 0:
        return 0.0

    for key in ("risk_percent_of_account", "allocated_pct_of_account", "size_percent_of_account"):
        v = trade.get(key)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    ov = trade.get("operator_size_override_percent")
    if ov is not None:
        try:
            return float(ov) * 100.0
        except (TypeError, ValueError):
            pass

    eq = trade.get("portfolio_equity") or trade.get("account_equity") or trade.get("account_balance")
    if eq is not None:
        try:
            e = float(eq)
            if e > 0:
                return (float(approved_notional) / e) * 100.0
        except (TypeError, ValueError):
            pass

    base_pct = float(DEFAULT_PHASE2_RISK.max_pct_per_trade) * 100.0
    if requested_notional is not None and float(requested_notional) > 0:
        return base_pct * (float(approved_notional) / float(requested_notional))
    return base_pct


def _target_or_ev_open(trade: Dict[str, Any]) -> Optional[str]:
    for k in ("profit_target_dollars", "target_price", "take_profit_price"):
        v = trade.get(k)
        if v is not None:
            fd = _fmt_dollars(v)
            if fd:
                return fd
            return str(v)
    ev = trade.get("expected_value")
    if ev is not None:
        try:
            e = float(ev)
            return f"EV {e * 100:.2f}%"
        except (TypeError, ValueError):
            return _nonempty_str(ev)
    return None


def _one_line_reason_open(trade: Dict[str, Any]) -> Optional[str]:
    for key in ("reasoning_text", "notes", "rationale", "reason"):
        s = _nonempty_str(trade.get(key))
        if s:
            return _truncate_line(s, 300)
    return None


def _one_line_close_reason(trade: Dict[str, Any]) -> Optional[str]:
    parts: List[str] = []
    er = trade.get("exit_reason")
    if er is not None and str(er).strip():
        parts.append(str(er).strip())
    for key in ("failure_reason", "reasoning_text", "notes"):
        s = _nonempty_str(trade.get(key))
        if s and s not in parts:
            parts.append(s)
    if not parts:
        return None
    merged = " — ".join(parts)
    return _truncate_line(merged, 300)


def _truncate_line(s: str, max_len: int) -> str:
    s = re.sub(r"\s+", " ", s.strip())
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def _open_requested_approved_from_meta(meta: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    req = meta.get("requested_size")
    appr = meta.get("approved_size")
    try:
        rq = float(req) if req is not None else None
    except (TypeError, ValueError):
        rq = None
    try:
        ap = float(appr) if appr is not None else None
    except (TypeError, ValueError):
        ap = None
    return rq, ap


def _size_adjustment_line(bucket: str, req: Optional[float], appr: Optional[float], meta: Dict[str, Any]) -> Optional[str]:
    st = str(meta.get("approval_status") or "")
    reason = str(meta.get("reason") or "")
    if bucket == "BLOCKED" or st == "BLOCKED":
        return "Size Adjustment: Blocked"
    if reason == "unknown_bucket_failsafe":
        return "Size Adjustment: Failsafe 50% (unknown bucket)"
    if st == "REDUCED" or (bucket == "REDUCED" and req is not None and appr is not None and appr < req - 0.001):
        return "Size Adjustment: Reduced 50%"
    if bucket == "NORMAL" and req is not None and appr is not None and abs(req - appr) < 0.005:
        return None
    if req is not None and appr is not None and abs(req - appr) > 0.001:
        return "Size Adjustment: Reduced 50%"
    return None


def _human_block_reason(decision: Dict[str, Any]) -> str:
    code = str(decision.get("reason") or "")
    if code == "hard_lockout_active":
        reasons = decision.get("lockout_reasons") or []
        if reasons:
            return "Hard lockout active: " + ", ".join(str(x) for x in reasons)
        return "Hard lockout active (daily / weekly / execution anomaly)."
    return {
        "risk_bucket_blocked": "Account risk bucket is BLOCKED; no new risk allocated.",
        "invalid_requested_size": "Invalid or missing requested size (capital_allocated / size_dollars / planned_risk).",
        "rounded_to_zero": "Approved size rounded to zero at current multiplier.",
        "sizing_logic_error": "Sizing logic error; open rejected (fail-safe).",
    }.get(code, code or "policy_block")


def format_trade_sizing_blocked_alert(trade_snapshot: Dict[str, Any], decision: Dict[str, Any]) -> str:
    """Telegram for placement blocked by policy (trade not logged). Distinct from TRADE OPEN."""
    lines: List[str] = ["Ezras — TRADE BLOCKED", ""]

    m = _nonempty_str(trade_snapshot.get("market"))
    if m:
        lines.append(f"Market: {m}")

    tick = _ticker_line(trade_snapshot)
    if tick:
        lines.append(f"Ticker: {tick}")

    side = _infer_side(trade_snapshot)
    if side and side != "—":
        lines.append(f"Side: {side}")

    lines.append("Risk Mode: BLOCKED")
    lines.append("Trading Status: DISABLED")
    if str(decision.get("reason") or "") == "hard_lockout_active":
        lines.append("Block Reason: " + _human_block_reason(decision))

    req = decision.get("requested_size")
    appr = decision.get("approved_size")
    rs = _fmt_dollars(req) if req is not None else "—"
    lines.append(f"Requested Size: {rs}")
    lines.append(f"Approved Size: {_fmt_dollars(appr) if appr is not None else '$0.00'}")
    lines.append("Size Adjustment: Blocked")

    ep = _fmt_entry_exit_price(trade_snapshot.get("entry_price"))
    if ep:
        lines.append(f"Entry: {ep}")

    lines.extend(["", f"Block Reason: {_human_block_reason(decision)}"])

    tid = _nonempty_str(trade_snapshot.get("trade_id")) or "—"
    lines.extend(["", f"Trade ID: {tid}", f"Time: {_format_timestamp(trade_snapshot.get('timestamp'))}"])

    return "\n".join(lines).strip() + "\n"


def format_trade_placed_message(trade: Dict[str, Any]) -> str:
    """
    Placed / open alert. Always canonicalizes sizing via shared policy (no drift from logs).

    If the effective decision is BLOCKED (preview / simulation), returns ``TRADE BLOCKED`` body,
    never ``TRADE OPEN``.
    """
    from trading_ai.automation.position_sizing_policy import normalize_position_sizing_meta

    normalize_position_sizing_meta(
        trade,
        source_path="telegram_placed_format",
        mutate_capital=False,
        record_audit=False,
    )
    meta = trade.get("position_sizing_meta") or {}
    st = str(meta.get("approval_status") or "")
    eff = str(meta.get("effective_bucket") or "")
    if st == "BLOCKED" or eff == "BLOCKED":
        snap = {
            k: trade.get(k)
            for k in ("trade_id", "market", "position", "timestamp", "entry_price", "ticker", "event_name")
        }
        return format_trade_sizing_blocked_alert(snap, meta)

    lines: List[str] = ["Ezras — TRADE OPEN", ""]

    m = _nonempty_str(trade.get("market"))
    if m:
        lines.append(f"Market: {m}")

    tick = _ticker_line(trade)
    if tick:
        lines.append(f"Ticker: {tick}")

    side = _infer_side(trade)
    bucket = str(meta.get("effective_bucket") or trade.get("risk_bucket_at_open") or "NORMAL")
    req_f, appr_f = _open_requested_approved_from_meta(meta)
    appr_st = meta.get("approval_status")

    if side and side != "—":
        lines.append(f"Side: {side}")
        acc_m = meta.get("account_risk_bucket")
        strat_m = meta.get("strategy_risk_bucket")
        if acc_m:
            lines.append(f"Account Risk Mode: {acc_m}")
        if strat_m:
            lines.append(f"Strategy Risk Mode: {strat_m}")
        lines.append(f"Effective Risk Mode: {bucket}")
        lines.append("Trading Status: DISABLED" if bucket == "BLOCKED" else "Trading Status: ACTIVE")
        rs = _fmt_dollars(req_f)
        ar = _fmt_dollars(appr_f)
        if rs:
            lines.append(f"Requested Size: {rs}")
        if ar:
            lines.append(f"Approved Size: {ar}")
        adj = _size_adjustment_line(bucket, req_f, appr_f, meta)
        if adj:
            lines.append(adj)
        if req_f is not None and appr_f is not None and abs(req_f - appr_f) > 0.001:
            lines.append(f"Requested Risk Basis: {_fmt_dollars(req_f)}")
            lines.append(f"Approved Risk Basis: {_fmt_dollars(appr_f)}")

    rp = _open_risk_percent_truthful(
        trade,
        approved_notional=appr_f,
        requested_notional=req_f,
        effective_bucket=eff,
        approval_status=str(appr_st) if appr_st is not None else None,
    )
    if rp is not None:
        lines.append(f"Risk: {rp:.1f}% of account")

    ep = _fmt_entry_exit_price(trade.get("entry_price"))
    if ep:
        lines.append(f"Entry: {ep}")

    ss = trade.get("signal_score")
    if ss is not None:
        try:
            lines.append(f"Signal Score: {int(ss)}")
        except (TypeError, ValueError):
            lines.append(f"Signal Score: {ss}")

    strat = _strategy_line(trade)
    if strat:
        lines.append(f"Strategy: {strat}")

    tev = _target_or_ev_open(trade)
    if tev:
        lines.append(f"Target / EV: {tev}")

    reason = _one_line_reason_open(trade)
    if reason:
        lines.extend(["", "Reason:", reason])

    tid = _nonempty_str(trade.get("trade_id")) or "—"
    lines.extend(["", f"Trade ID: {tid}", f"Time: {_format_timestamp(trade.get('timestamp'))}"])

    return "\n".join(lines).strip() + "\n"


def _payout_amount_dollars(trade: Dict[str, Any]) -> Optional[float]:
    """Total settlement / cash back (stake + net P&L) when not explicit."""
    raw = trade.get("payout_dollars")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    cap = float(trade.get("capital_allocated") or 0.0)
    net = trade.get("net_pnl_dollars")
    if net is None:
        net = trade.get("gross_pnl_dollars")
    if net is not None:
        try:
            return cap + float(net)
        except (TypeError, ValueError):
            pass
    return None


def format_trade_closed_message(trade: Dict[str, Any]) -> str:
    """
    Settlement / payout alert — same headline block order as :func:`format_trade_placed_message`
    (Market → Ticker → Side), then P&L and payout, optional risk/exit details, Trade ID + Time.
    """
    lines: List[str] = ["Ezras — TRADE CLOSED / PAYOUT", ""]

    m = _nonempty_str(trade.get("market"))
    if m:
        lines.append(f"Market: {m}")

    tick = _ticker_line(trade)
    if tick:
        lines.append(f"Ticker: {tick}")

    side = _infer_side(trade)
    if side and side != "—":
        lines.append(f"Side: {side}")

    cap = float(trade.get("capital_allocated") or 0.0)
    roi = float(trade.get("roi_percent") or 0.0)

    gross = trade.get("gross_pnl_dollars")
    net = trade.get("net_pnl_dollars")
    if gross is None and cap:
        gross = cap * (roi / 100.0)
    if net is None and cap:
        net = cap * (roi / 100.0)

    if n := _fmt_dollars(net):
        lines.append(f"P&L (net): {n}")

    if gross is not None:
        try:
            gn = float(net) if net is not None else None
            show_gross = gn is None or abs(float(gross) - gn) > 1e-6
        except (TypeError, ValueError):
            show_gross = True
        if show_gross and (g := _fmt_dollars(gross)):
            lines.append(f"P&L (gross): {g}")

    pay = _fmt_dollars(_payout_amount_dollars(trade))
    if pay:
        lines.append(f"Payout amount: {pay}")

    xp = _fmt_entry_exit_price(trade.get("exit_price"))
    if xp:
        lines.append(f"Exit: {xp}")

    roi_s = _fmt_percent(trade.get("roi_percent"), decimals=2)
    if roi_s:
        lines.append(f"ROI: {roi_s}")

    cost = trade.get("total_execution_cost_dollars")
    if cost is None:
        cost = trade.get("execution_cost_dollars")
    cst = _fmt_dollars(cost)
    if cst:
        lines.append(f"Execution Cost: {cst}")

    strat = _strategy_line(trade)
    if strat:
        lines.append(f"Strategy: {strat}")

    try:
        from trading_ai.automation.risk_bucket import get_account_risk_bucket

        bucket_after = get_account_risk_bucket({"phase": "closed", "trade": trade})
    except Exception:
        bucket_after = "REDUCED"

    lines.append(f"Risk Mode After Close: {bucket_after}")
    bucket_before = trade.get("risk_bucket_at_open")
    if bucket_before and str(bucket_before) != str(bucket_after):
        lines.append(f"Bucket Change: {bucket_before} → {bucket_after}")

    creason = _one_line_close_reason(trade)
    if creason:
        lines.extend(["", "Close Reason:", creason])

    tid = _nonempty_str(trade.get("trade_id")) or "—"
    lines.extend(["", f"Trade ID: {tid}", f"Time: {_format_timestamp(trade.get('timestamp'))}"])

    return "\n".join(lines).strip() + "\n"


def maybe_notify_trade_placed(settings: Optional[Any] = None, trade: Optional[Dict[str, Any]] = None) -> None:
    """Phase 2 hook → post-trade hub (Telegram + runtime log). Never raises."""
    if not trade:
        return
    try:
        from trading_ai.automation.post_trade_hub import execute_post_trade_placed

        execute_post_trade_placed(settings, trade)
    except Exception:
        logger.exception("post_trade placed hook failed (non-fatal)")


def maybe_notify_trade_closed(settings: Optional[Any] = None, trade: Optional[Dict[str, Any]] = None) -> None:
    if not trade:
        return
    try:
        from trading_ai.automation.post_trade_hub import execute_post_trade_closed

        execute_post_trade_closed(settings, trade)
    except Exception:
        logger.exception("post_trade closed hook failed (non-fatal)")
