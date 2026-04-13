"""
Trade placed / closed → Telegram (non-blocking hooks from Phase 2 trade_ops).

Message copy is formatted for a concise operator / lock-screen feed; transport + idempotency unchanged.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from trading_ai.phase2.config_phase2 import DEFAULT_PHASE2_RISK

logger = logging.getLogger(__name__)


def _risk_mode_block(trade: Dict[str, Any], *, phase: str) -> List[str]:
    """Lines for Risk Mode (+ trading disabled hint when BLOCKED)."""
    try:
        from trading_ai.automation.risk_bucket import get_account_risk_bucket

        bucket = get_account_risk_bucket({"phase": phase, "trade": trade})
    except Exception:
        bucket = "NORMAL"
    b = bucket if bucket in ("NORMAL", "REDUCED", "BLOCKED") else "NORMAL"
    out = [f"Risk Mode: {b}"]
    if b == "BLOCKED":
        out.append("Trading Disabled")
    return out


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
    """Bucket / theme labels only — avoids duplicating ticker/event_name."""
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


def format_trade_placed_message(trade: Dict[str, Any]) -> str:
    """Hedge-fund style OPEN alert; omits empty optional fields."""
    lines: List[str] = ["Ezras — TRADE OPEN", ""]

    m = _nonempty_str(trade.get("market"))
    if m:
        lines.append(f"Market: {m}")

    tick = _ticker_line(trade)
    if tick:
        lines.append(f"Ticker: {tick}")

    side = _infer_side(trade)
    if side and side != "—":
        lines.append(f"Side: {side}")
        lines.extend(_risk_mode_block(trade, phase="open"))

    sz = _fmt_dollars(trade.get("capital_allocated"))
    if sz:
        lines.append(f"Size: {sz}")

    rp = _risk_percent_of_account(trade)
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


def format_trade_closed_message(trade: Dict[str, Any]) -> str:
    """Hedge-fund style CLOSED alert; omits empty optional fields."""
    lines: List[str] = ["Ezras — TRADE CLOSED", ""]

    m = _nonempty_str(trade.get("market"))
    if m:
        lines.append(f"Market: {m}")

    tick = _ticker_line(trade)
    if tick:
        lines.append(f"Ticker: {tick}")

    side = _infer_side(trade)
    if side and side != "—":
        lines.append(f"Side: {side}")
        lines.extend(_risk_mode_block(trade, phase="closed"))

    xp = _fmt_entry_exit_price(trade.get("exit_price"))
    if xp:
        lines.append(f"Exit: {xp}")

    cap = float(trade.get("capital_allocated") or 0.0)
    roi = float(trade.get("roi_percent") or 0.0)

    gross = trade.get("gross_pnl_dollars")
    net = trade.get("net_pnl_dollars")
    if gross is None and cap:
        gross = cap * (roi / 100.0)
    if net is None and cap:
        net = cap * (roi / 100.0)

    g = _fmt_dollars(gross)
    if g:
        lines.append(f"Gross P&L: {g}")

    n = _fmt_dollars(net)
    if n:
        lines.append(f"Net P&L: {n}")

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
