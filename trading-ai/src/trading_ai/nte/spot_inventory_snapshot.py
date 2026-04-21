"""
Live Coinbase spot ledger snapshot: quote, base inventory, internal open-position base.

Used by reconciliation proof, micro-validation, and operator-facing status lines.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Mapping, Optional, Tuple

from trading_ai.shark.state_store import load_positions


def parse_spot_product(product_id: str) -> Tuple[str, str]:
    """``BTC-USD`` → ``(\"BTC\", \"USD\")``; falls back to ``(\"BTC\", \"USD\")``."""
    s = (product_id or "").strip().upper()
    if "-" in s:
        a, b = s.split("-", 1)
        return a.strip(), b.strip()
    return "BTC", "USD"


def exchange_currency_qty(accounts: List[Dict[str, Any]], currency: str) -> float:
    """Sum available balance for ``currency`` across Coinbase ``/accounts`` rows."""
    cur_u = currency.upper()
    total = 0.0
    for a in accounts:
        if not isinstance(a, dict):
            continue
        if str(a.get("currency") or "").upper() != cur_u:
            continue
        av = a.get("available_balance") or a.get("balance") or {}
        if isinstance(av, dict):
            try:
                total += float(av.get("value") or 0.0)
            except (TypeError, ValueError):
                continue
        else:
            try:
                total += float(av or 0.0)
            except (TypeError, ValueError):
                continue
    return total


def usd_usdc_from_accounts(accounts: List[Dict[str, Any]]) -> Tuple[float, float, float]:
    """Returns ``(usd, usdc, combined)`` available balances."""
    u = exchange_currency_qty(accounts, "USD")
    c = exchange_currency_qty(accounts, "USDC")
    return u, c, u + c


def internal_open_base_qty_for_asset(positions: Mapping[str, Any], base_ccy: str) -> float:
    """
    Sum absolute base quantity for open Coinbase spot-like positions whose product
    base matches ``base_ccy`` (e.g. BTC for BTC-USD).
    """
    b = base_ccy.upper()
    total = 0.0
    for p in positions.get("open_positions") or []:
        if not isinstance(p, dict):
            continue
        outlet = str(p.get("outlet") or p.get("venue") or "").lower()
        if "coin" not in outlet and outlet != "coinbase":
            continue
        pid = str(p.get("product_id") or p.get("asset") or "").upper()
        base_from_pid = pid.split("-")[0] if "-" in pid else ""
        if base_from_pid and base_from_pid != b:
            continue
        extra = str(p.get("base_currency") or p.get("base_ccy") or "").upper()
        if extra and extra != b:
            continue
        for key in ("base_qty", "qty", "size", "contracts", "base_size"):
            if p.get(key) is not None:
                try:
                    total += abs(float(p[key]))
                except (TypeError, ValueError):
                    pass
                break
    return total


def snapshot_live_spot_ledger(
    product_id: str = "BTC-USD",
    *,
    mark_price_usd_per_base: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Exchange + internal spot snapshot for a product (no orders).

    When Coinbase credentials are missing, returns zeros and ``source=no_credentials``.
    """
    base_a, quote_a = parse_spot_product(product_id)
    out: Dict[str, Any] = {
        "product_id": product_id.strip().upper(),
        "validation_base_asset": base_a,
        "validation_quote_asset": quote_a,
        "exchange_base_qty": 0.0,
        "quote_available_usd": 0.0,
        "quote_available_usdc": 0.0,
        "quote_available_combined_usd": 0.0,
        "internal_base_qty": 0.0,
        "base_inventory_market_value_usd": None,
        "total_spot_equity_usd": None,
        "imported_inventory_baseline": False,
        "source": "uninitialized",
    }
    positions = load_positions()
    out["internal_base_qty"] = internal_open_base_qty_for_asset(positions, base_a)

    try:
        from trading_ai.shark.outlets.coinbase import CoinbaseClient

        cc = CoinbaseClient()
        if not cc.has_credentials():
            out["source"] = "no_credentials"
            return out
        accounts = cc.list_all_accounts()
    except Exception as exc:
        out["source"] = f"fetch_failed:{type(exc).__name__}"
        return out

    out["exchange_base_qty"] = exchange_currency_qty(accounts, base_a)
    u, usdc, comb = usd_usdc_from_accounts(accounts)
    out["quote_available_usd"] = u
    out["quote_available_usdc"] = usdc
    out["quote_available_combined_usd"] = comb

    mp = mark_price_usd_per_base
    if mp is None and base_a:
        try:
            from trading_ai.shark.outlets import coinbase as cb_mod

            j = cb_mod._brokerage_public_request(f"/market/products/{product_id.strip().upper()}/ticker")
            if isinstance(j, dict):
                for k in ("price", "best_bid", "best_ask"):
                    v = j.get(k)
                    if v is not None:
                        mp = float(v)
                        break
        except Exception:
            mp = None

    if mp is not None and mp > 0:
        mv = float(out["exchange_base_qty"]) * float(mp)
        out["base_inventory_market_value_usd"] = mv
        out["total_spot_equity_usd"] = float(comb) + mv
    out["source"] = "coinbase_api"
    out["imported_inventory_baseline"] = (os.environ.get("SPOT_IMPORT_EXCHANGE_BASELINE") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    return out
