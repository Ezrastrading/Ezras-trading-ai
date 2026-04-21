"""Executable capital = quote asset available for the product's pair (never base-as-quote)."""

from __future__ import annotations

import os
from typing import Any, Dict, Tuple

from trading_ai.nte.execution.product_rules import venue_min_notional_usd
from trading_ai.safety.error_taxonomy import ExecutionErrorCode

_KNOWN_QUOTE_SUFFIXES = ("USDC", "USD", "EUR", "GBP", "USDT")


def _parse_spot_base_quote(product_id: str) -> tuple[str, str]:
    """Local copy — avoids importing ``nte.execution.routing`` (heavy side effects)."""
    raw = (product_id or "").strip().upper()
    if not raw or "-" not in raw:
        return raw or "UNKNOWN", "USD"
    for suf in _KNOWN_QUOTE_SUFFIXES:
        if raw.endswith("-" + suf):
            base = raw[: -(len(suf) + 1)]
            return base, suf
    base, _, quote = raw.rpartition("-")
    return base or raw, quote or "USD"


def _fee_buffer_pct() -> float:
    """
    Conservative preflight buffer for fees + minor price drift.

    Default 0.3% (30 bps). Tunable via ``EZRAS_COINBASE_FEE_BUFFER_PCT``.
    """
    raw = (os.environ.get("EZRAS_COINBASE_FEE_BUFFER_PCT") or "").strip()
    if not raw:
        return 0.003
    try:
        v = float(raw)
    except ValueError:
        return 0.003
    return max(0.0, min(0.05, v))


def _effective_quote_available(quote: str, bal: Dict[str, float]) -> float:
    """
    Spendable quote for a spot pair.

    Strict per-quote currency availability. USD and USDC are not automatically interchangeable
    for *execution* unless the product itself is quoted in that currency (e.g. BTC-USDC).

    (The product selection layer may choose a USDC-quoted sibling product when only USDC is funded.)
    """
    q = (quote or "").strip().upper()
    return float(bal.get(q) or 0.0)


def _required_quote_with_buffer(product_id: str, requested_quote: float) -> float:
    base_need = max(float(requested_quote or 0.0), float(venue_min_notional_usd(product_id)))
    return float(base_need) * (1.0 + _fee_buffer_pct())


def assert_executable_capital_for_product(
    product_id: str,
    *,
    requested_quote: float,
    quote_balances_by_ccy: Dict[str, float],
    multi_leg: bool = False,
) -> Tuple[bool, str]:
    """
    HARD invariant: spendable quote for this product must cover max(requested, venue_min) plus a fee buffer.
    Never treats BTC/ETH base wallets as USD/USDC spendable.
    """
    if multi_leg:
        return False, ExecutionErrorCode.MULTI_LEG_EXECUTION_NOT_ENABLED.value
    _, quote = _parse_spot_base_quote(product_id)
    avail = _effective_quote_available(quote, quote_balances_by_ccy)
    need = _required_quote_with_buffer(product_id, float(requested_quote or 0.0))
    if avail + 1e-9 < need:
        return False, ExecutionErrorCode.INSUFFICIENT_ALLOWED_QUOTE_BALANCE.value
    return True, "ok"


def explain_capital_truth_violation(
    product_id: str,
    *,
    requested_quote: float,
    quote_balances_by_ccy: Dict[str, float],
) -> Dict[str, Any]:
    _, quote = _parse_spot_base_quote(product_id)
    base_need = max(float(requested_quote or 0.0), float(venue_min_notional_usd(product_id)))
    need = _required_quote_with_buffer(product_id, float(requested_quote or 0.0))
    return {
        "product_id": product_id,
        "quote_asset": quote,
        "required_quote": float(need),
        "required_quote_base": float(base_need),
        "fee_buffer_pct": float(_fee_buffer_pct()),
        "available_quote_for_asset": float(_effective_quote_available(quote, quote_balances_by_ccy)),
        "available_quote_by_currency": {
            "USD": float(quote_balances_by_ccy.get("USD") or 0.0),
            "USDC": float(quote_balances_by_ccy.get("USDC") or 0.0),
        },
        "note": "Portfolio mark or base-asset balance must not be used as spendable quote.",
    }
