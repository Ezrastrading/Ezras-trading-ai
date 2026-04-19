"""
Paginated Coinbase account listing and quote balance helpers for live validation.

Uses :meth:`trading_ai.shark.outlets.coinbase.CoinbaseClient.list_all_accounts`.
"""

from __future__ import annotations

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
