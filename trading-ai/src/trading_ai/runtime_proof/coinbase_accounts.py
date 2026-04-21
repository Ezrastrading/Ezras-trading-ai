"""
Paginated Coinbase account listing and quote balance helpers for live validation.

Uses :meth:`trading_ai.shark.outlets.coinbase.CoinbaseClient.list_all_accounts`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from trading_ai.shark.outlets.coinbase import CoinbaseClient


def get_all_coinbase_accounts(client: CoinbaseClient) -> List[Dict[str, Any]]:
    """Return every ``/accounts`` row (all pagination pages)."""
    return client.list_all_accounts()


def get_available_quote_balances(client: CoinbaseClient) -> Dict[str, float]:
    """
    Aggregate available USD and USDC across all wallets (after full pagination).

    Returns ``{"USD": float, "USDC": float}`` using the same spendable extraction as
    :meth:`CoinbaseClient._account_usd_usdc_spendable`.
    """
    from trading_ai.shark.outlets.coinbase import CoinbaseClient

    usd = 0.0
    usdc = 0.0
    for a in get_all_coinbase_accounts(client):
        if not isinstance(a, dict):
            continue
        curr = str(a.get("currency") or "").upper()
        if curr not in ("USD", "USDC"):
            continue
        v = CoinbaseClient._account_usd_usdc_spendable(a)
        if curr == "USD":
            usd += v
        else:
            usdc += v
    return {"USD": usd, "USDC": usdc}


def _btc_usdc_spot_tradable() -> bool:
    """Public market ticker for BTC-USDC — confirms product exists and is queryable."""
    try:
        from trading_ai.shark.outlets import coinbase as cb_mod

        j = cb_mod._brokerage_public_request("/market/products/BTC-USDC/ticker")
        if not isinstance(j, dict):
            return False
        return bool(j.get("price") or j.get("best_ask") or j.get("best_bid"))
    except Exception:
        return False


def _product_spot_tradable_public(product_id: str) -> bool:
    """Whether ``product_id`` appears spot-tradable via public market data (tests patch this)."""
    pid = (product_id or "").strip().upper()
    if pid == "BTC-USDC":
        return _btc_usdc_spot_tradable()
    if "-" in pid and (pid.endswith("-USD") or pid.endswith("-USDC")):
        return True
    return False


def resolve_validation_market_product(
    client: CoinbaseClient,
    *,
    quote_notional: float,
    preferred_product_id: str = "BTC-USD",
) -> Tuple[str, Dict[str, Any], Optional[str]]:
    """
    Choose BTC-USD vs BTC-USDC for validation using paginated balances.

    Returns ``(product_id, diagnostics, error)`` where ``error`` is ``None`` if funds suffice.

    Preference: USD balance covers notional → keep ``preferred_product_id`` if it ends with
    ``-USD`` (e.g. BTC-USD); else default ``BTC-USD``. If USD insufficient but USDC covers
    notional and BTC-USDC is tradable → ``BTC-USDC``.
    """
    bal = get_available_quote_balances(client)
    needed = float(quote_notional)
    diag: Dict[str, Any] = {
        "quote_balances": bal,
        "quote_notional": needed,
        "preferred_product_id": preferred_product_id,
    }

    if bal.get("USD", 0.0) >= needed:
        pref = (preferred_product_id or "BTC-USD").strip().upper()
        if pref.endswith("-USD"):
            return pref, {**diag, "chosen_reason": "usd_covers_notional_usd_pair"}, None
        return "BTC-USD", {**diag, "chosen_reason": "usd_covers_default_BTC_USD"}, None

    if bal.get("USDC", 0.0) >= needed and _btc_usdc_spot_tradable():
        return "BTC-USDC", {**diag, "chosen_reason": "usdc_fallback_BTC_USDC"}, None

    return (
        "BTC-USD",
        {**diag, "chosen_reason": "insufficient_quote"},
        "insufficient_USD_or_USDC_for_notional",
    )


def get_quote_balances_by_currency(client: CoinbaseClient) -> Dict[str, float]:
    """Alias for paginated quote balance extraction (USD + USDC)."""
    return get_available_quote_balances(client)


def get_spendable_balances_by_currency_all(client: CoinbaseClient) -> Dict[str, float]:
    """Spendable USD/USDC map — coherent validation resolution entry point."""
    return get_available_quote_balances(client)


def preflight_exact_spot_product(
    client: CoinbaseClient,
    *,
    product_id: str,
    quote_notional: float,
    runtime_root: Path,
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    """
    Validate venue min-notional + local order-size rules before placing a live order.

    Returns ``(ok, diagnostics, error_message)``.
    """
    from trading_ai.nte.execution.product_rules import validate_order_size, venue_min_notional_usd

    _ = runtime_root
    pid = (product_id or "").strip().upper()
    diag: Dict[str, Any] = {"product_id": pid, "quote_notional": float(quote_notional)}
    try:
        bal = get_available_quote_balances(client)
        diag["quote_balances"] = bal
        vmin = float(venue_min_notional_usd(pid))
        need = max(float(quote_notional), vmin)
        usd = float(bal.get("USD") or 0.0)
        usdc = float(bal.get("USDC") or 0.0)
        spendable = usd if pid.endswith("-USD") else usdc if pid.endswith("-USDC") else usd + usdc
        diag["spendable_quote_assessed"] = spendable
        if spendable + 1e-9 < need:
            return False, diag, "insufficient_quote_balance"
        ok, reason = validate_order_size(pid, quote_notional_usd=float(quote_notional))
        if not ok:
            return False, diag, reason or "size_invalid"
        diag["has_credentials"] = bool(getattr(client, "has_credentials", lambda: False)())
        return True, diag, None
    except Exception as exc:
        return False, diag, f"{type(exc).__name__}:{exc}"
