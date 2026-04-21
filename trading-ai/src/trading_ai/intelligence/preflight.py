"""
Build trade context and run intelligence gates (no venue submits).

Enabled when ``EZRAS_TRADING_INTELLIGENCE=1`` (default off for backward compatibility).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.intelligence.confidence_filter import passes_confidence
from trading_ai.intelligence.cooldown import cooldown_active
from trading_ai.intelligence.edge_filter import passes_edge_filter
from trading_ai.intelligence.market_filter import passes_market_conditions
from trading_ai.intelligence.trade_gate import should_execute_trade

logger = logging.getLogger(__name__)


def trading_intelligence_enabled() -> bool:
    return (os.environ.get("EZRAS_TRADING_INTELLIGENCE") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def strict_intelligence_inputs() -> bool:
    """When set, missing edge/market meta blocks the trade instead of skipping that check."""
    return (os.environ.get("EZRAS_TRADING_INTELLIGENCE_STRICT") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _float_meta(meta: Dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for k in keys:
        if k in meta and meta[k] is not None:
            try:
                return float(meta[k])
            except (TypeError, ValueError):
                continue
    return default


def build_intelligence_context(
    *,
    intent_meta: Dict[str, Any],
    system_ok: bool,
    bid: Optional[float] = None,
    ask: Optional[float] = None,
    mid: Optional[float] = None,
    liquidity: Optional[float] = None,
    trade_size: Optional[float] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """Returns (context dict, skip_reasons for logging)."""
    reasons: List[str] = []
    edge_ok = True
    market_ok = True

    exp_profit = _float_meta(intent_meta, "expected_profit_usd", "expected_edge_usd")
    est_fees = _float_meta(intent_meta, "estimated_fees_usd", "est_fees_usd")

    if exp_profit > 0 or est_fees > 0 or strict_intelligence_inputs():
        if exp_profit <= 0 and est_fees <= 0 and strict_intelligence_inputs():
            edge_ok = False
            reasons.append("edge_meta_missing")
        else:
            ok_e, er = passes_edge_filter(exp_profit, est_fees)
            edge_ok = ok_e
            if not ok_e and er:
                reasons.append(er)
    else:
        reasons.append("edge_check_skipped_no_meta")

    if bid is not None and ask is not None and mid is not None and liquidity is not None and trade_size is not None:
        ok_m, mr = passes_market_conditions(bid, ask, mid, liquidity, trade_size)
        market_ok = ok_m
        if not ok_m and mr:
            reasons.append(mr)
    elif strict_intelligence_inputs():
        market_ok = False
        reasons.append("market_meta_missing")

    conf = _float_meta(intent_meta, "confidence", "confidence_score", default=1.0)
    ok_c, cr = passes_confidence(conf)
    confidence_ok = ok_c
    if not ok_c and cr:
        reasons.append(cr)

    cd_active = cooldown_active()
    cooldown_ok = not cd_active
    if cd_active:
        reasons.append("cooldown_active")

    context = {
        "edge_ok": edge_ok,
        "market_ok": market_ok,
        "cooldown_ok": cooldown_ok,
        "confidence_ok": confidence_ok,
        "system_ok": system_ok,
    }
    return context, reasons


def maybe_fetch_coinbase_microstructure(product_id: str) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    try:
        from trading_ai.shark.outlets.coinbase import CoinbaseClient

        c = CoinbaseClient()
        bid, ask = c.get_product_price(product_id)
        bid_f, ask_f = float(bid), float(ask)
        mid = (bid_f + ask_f) / 2.0 if bid_f > 0 and ask_f > 0 else None
        return bid_f, ask_f, mid
    except Exception as exc:
        logger.debug("intelligence coinbase microstructure skipped: %s", exc)
        return None, None, None


def run_intelligence_preflight(
    *,
    outlet: str,
    intent_meta: Dict[str, Any],
    system_ok: bool,
    notional_usd: float,
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Returns (allow, block_reason or \"\", context).

    When not ``trading_intelligence_enabled()``, returns (True, \"\", {}).
    """
    if not trading_intelligence_enabled():
        return True, "", {}

    try:
        from trading_ai.intelligence.overtrading_guard import overtrading_should_block

        ot_block, ot_reason = overtrading_should_block()
        if ot_block:
            return False, ot_reason or "intelligence:overtrading", {"overtrading": True}
    except Exception as exc:
        logger.debug("overtrading guard skipped: %s", exc)

    bid = ask = mid = None
    liquidity: Optional[float] = None
    trade_size = max(notional_usd, 1e-9)

    o = (outlet or "").lower()
    pid = str(intent_meta.get("product_id") or intent_meta.get("market_id") or "").strip()
    if o == "coinbase" and pid:
        bid, ask, mid = maybe_fetch_coinbase_microstructure(pid)
        liquidity = _float_meta(intent_meta, "liquidity_usd", "book_liquidity_usd", default=1e12)

    ctx, _notes = build_intelligence_context(
        intent_meta=intent_meta,
        system_ok=system_ok,
        bid=bid,
        ask=ask,
        mid=mid,
        liquidity=liquidity,
        trade_size=trade_size,
    )
    ok = should_execute_trade(ctx)
    if ok:
        return True, "", ctx

    # First failing dimension for operator message
    if not ctx.get("edge_ok"):
        return False, "intelligence:edge_too_small_or_meta", ctx
    if not ctx.get("market_ok"):
        return False, "intelligence:market_conditions", ctx
    if not ctx.get("cooldown_ok"):
        return False, "intelligence:cooldown", ctx
    if not ctx.get("confidence_ok"):
        return False, "intelligence:low_confidence", ctx
    if not ctx.get("system_ok"):
        return False, "intelligence:system_not_ok", ctx
    return False, "intelligence:blocked", ctx
