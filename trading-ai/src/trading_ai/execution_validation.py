"""
Runtime execution validation layer.

Blocks trade when:
- exchange unreachable
- quote size invalid
- balance < venue minimum notional
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class ExecutionValidationResult:
    ok: bool
    reason: str
    meta: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": bool(self.ok), "reason": self.reason, "meta": dict(self.meta)}


def validate_quote_size(quote_size: float, *, min_notional: float) -> Tuple[bool, str]:
    try:
        q = float(quote_size)
    except Exception:
        return False, "quote_size_not_numeric"
    if q <= 0:
        return False, "quote_size_non_positive"
    if q + 1e-12 < float(min_notional):
        return False, "below_min_notional"
    return True, "ok"


def validate_exchange_reachable(client: Any, symbol: str) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Minimal reachability: must be able to fetch a price/ticker.
    """
    try:
        bid, ask = client.get_product_price(symbol)
        bid_f = float(bid or 0.0)
        ask_f = float(ask or 0.0)
        if bid_f <= 0 and ask_f <= 0:
            return False, "ticker_unavailable", {"bid": bid_f, "ask": ask_f}
        return True, "ok", {"bid": bid_f, "ask": ask_f}
    except Exception as exc:
        return False, f"exchange_unreachable:{type(exc).__name__}", {"error": str(exc)}


def validate_balance_min_notional(
    client: Any, symbol: str, quote_size: float, *, min_notional: float
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Venue-agnostic contract: client must expose `get_fiat_balances_by_currency()` for (USD,USDC)
    or `get_usd_balance()` fallback.
    """
    quote = float(quote_size)
    need = max(float(min_notional), quote)
    meta: Dict[str, Any] = {"needed_quote": need, "symbol": symbol}
    try:
        if hasattr(client, "get_fiat_balances_by_currency"):
            by = client.get_fiat_balances_by_currency()
            usd = float((by or {}).get("USD") or 0.0)
            usdc = float((by or {}).get("USDC") or 0.0)
            meta.update({"usd_available": usd, "usdc_available": usdc})
            # Decide which quote currency the symbol consumes.
            sym = str(symbol).upper()
            if sym.endswith("-USDC"):
                ok = usdc + 1e-9 >= need
                return ok, ("ok" if ok else "insufficient_usdc_balance"), meta
            ok = usd + 1e-9 >= need
            return ok, ("ok" if ok else "insufficient_usd_balance"), meta
        if hasattr(client, "get_usd_balance"):
            bal = float(client.get_usd_balance() or 0.0)
            meta["usd_total_available"] = bal
            ok = bal + 1e-9 >= need
            return ok, ("ok" if ok else "insufficient_balance"), meta
    except Exception as exc:
        return False, f"balance_check_failed:{type(exc).__name__}", {**meta, "error": str(exc)}
    return False, "balance_api_unavailable", meta


def validate_runtime_pretrade(
    *,
    venue: str,
    client: Any,
    symbol: str,
    quote_size: float,
    min_notional: float,
) -> ExecutionValidationResult:
    ok_q, why_q = validate_quote_size(quote_size, min_notional=min_notional)
    if not ok_q:
        return ExecutionValidationResult(False, f"invalid_quote_size:{why_q}", {"venue": venue})

    ok_ex, why_ex, ex_meta = validate_exchange_reachable(client, symbol)
    if not ok_ex:
        return ExecutionValidationResult(False, why_ex, {"venue": venue, **ex_meta})

    ok_bal, why_bal, bal_meta = validate_balance_min_notional(
        client, symbol, quote_size, min_notional=min_notional
    )
    if not ok_bal:
        return ExecutionValidationResult(False, why_bal, {"venue": venue, **ex_meta, **bal_meta})

    return ExecutionValidationResult(True, "ok", {"venue": venue, **ex_meta, **bal_meta})

