"""Wallet inventory rows — base for universal portfolio truth (extend with mark-to-USD later)."""

from __future__ import annotations

from typing import Any, Dict, List

from trading_ai.shark.outlets.coinbase import CoinbaseClient


def build_wallet_inventory_rows(client: CoinbaseClient) -> List[Dict[str, Any]]:
    """
    Raw per-currency rows from paginated ``/accounts`` (available balance when present).

    Used for operator diagnostics and future executable-capital routing; does not invent products.
    """
    rows: List[Dict[str, Any]] = []
    for a in client.list_all_accounts():
        if not isinstance(a, dict):
            continue
        cur = str(a.get("currency") or "").upper()
        if not cur:
            continue
        avail = a.get("available_balance") or {}
        tot = a.get("balance") or {}
        try:
            av = float((avail.get("value") if isinstance(avail, dict) else None) or 0)
        except (TypeError, ValueError):
            av = 0.0
        rows.append(
            {
                "currency": cur,
                "available": av,
                "raw_account_uuid": a.get("uuid"),
            }
        )
    return rows
