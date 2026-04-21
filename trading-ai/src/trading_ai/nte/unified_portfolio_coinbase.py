"""
Unified Coinbase portfolio: USD + USDC + all wallet balances marked to USD where a ``{ASSET}-USD`` product exists.

Trading capital for risk sizing should use ``total_usd_value`` (not quote cash alone).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, Optional

logger = logging.getLogger(__name__)


def _ticker_mid_usd(product_id: str) -> Optional[float]:
    try:
        from trading_ai.shark.outlets import coinbase as cb_mod

        j = cb_mod._brokerage_public_request(f"/market/products/{product_id}/ticker")
        if not isinstance(j, dict):
            return None
        for k in ("price", "best_bid", "best_ask"):
            v = j.get(k)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    continue
        return None
    except Exception as exc:
        logger.debug("ticker %s: %s", product_id, exc)
        return None


def build_unified_coinbase_portfolio_usd(
    accounts: List[Dict[str, Any]],
    *,
    include_stable_as_cash: bool = True,
) -> Dict[str, Any]:
    """
    Aggregate balances into ``crypto_positions_map`` (qty) and ``usd_equivalent_by_asset``.

    Unknown / unpriced assets are listed under ``unpriced_assets`` with raw qty only.
    """
    from trading_ai.nte.spot_inventory_snapshot import exchange_currency_qty, usd_usdc_from_accounts

    usd, usdc, _ = usd_usdc_from_accounts(accounts)
    crypto_positions_map: Dict[str, float] = {}
    usd_equiv: Dict[str, float] = {}
    unpriced: List[str] = []

    for a in accounts:
        if not isinstance(a, dict):
            continue
        cur = str(a.get("currency") or "").upper()
        if not cur:
            continue
        av = a.get("available_balance") or a.get("balance") or {}
        if isinstance(av, dict):
            try:
                qty = float(av.get("value") or 0.0)
            except (TypeError, ValueError):
                qty = 0.0
        else:
            try:
                qty = float(av or 0.0)
            except (TypeError, ValueError):
                qty = 0.0
        if qty <= 0:
            continue
        crypto_positions_map[cur] = crypto_positions_map.get(cur, 0.0) + qty

    cash_usd = float(usd)
    if include_stable_as_cash:
        cash_usd += float(usdc)
    usd_equiv["USD"] = float(usd)
    usd_equiv["USDC"] = float(usdc)

    total = cash_usd
    for asset, qty in crypto_positions_map.items():
        if asset in ("USD", "USDC"):
            continue
        pid = f"{asset}-USD"
        px = _ticker_mid_usd(pid)
        if px is not None and px > 0:
            v = qty * px
            usd_equiv[asset] = v
            total += v
        else:
            unpriced.append(asset)

    return {
        "total_usd_value": total,
        "cash_usd_including_usdc": cash_usd,
        "crypto_positions_map": dict(sorted(crypto_positions_map.items())),
        "usd_equivalent_by_asset": usd_equiv,
        "unpriced_assets": unpriced,
        "source": "coinbase_accounts_plus_ticker",
    }


def load_unified_portfolio_from_client() -> Dict[str, Any]:
    """Fetch accounts via :class:`~trading_ai.shark.outlets.coinbase.CoinbaseClient`."""
    try:
        from trading_ai.shark.outlets.coinbase import CoinbaseClient

        cc = CoinbaseClient()
        if not cc.has_credentials():
            return {
                "total_usd_value": 0.0,
                "cash_usd_including_usdc": 0.0,
                "crypto_positions_map": {},
                "usd_equivalent_by_asset": {},
                "unpriced_assets": [],
                "source": "no_credentials",
            }
        accts = cc.list_all_accounts()
        return build_unified_coinbase_portfolio_usd(accts)
    except Exception as exc:
        return {
            "total_usd_value": 0.0,
            "cash_usd_including_usdc": 0.0,
            "crypto_positions_map": {},
            "usd_equivalent_by_asset": {},
            "unpriced_assets": [],
            "source": f"error:{type(exc).__name__}",
        }


def portfolio_vs_internal_open_positions_hint(
    unified: Mapping[str, Any],
    positions: Mapping[str, Any],
) -> Dict[str, Any]:
    """
    Read-only reconciliation hints: which base assets differ between exchange qty and open positions.

    Does not mutate state — operator or a dedicated sync job must apply corrections.
    """
    from trading_ai.nte.spot_inventory_snapshot import internal_open_base_qty_for_asset

    cmap = unified.get("crypto_positions_map") or {}
    hints: List[Dict[str, Any]] = []
    for asset in sorted(set(cmap.keys()) | {"BTC", "ETH"}):
        if asset in ("USD", "USDC"):
            continue
        ex = float(cmap.get(asset) or 0.0)
        inn = internal_open_base_qty_for_asset(positions, asset)
        d = ex - inn
        if abs(d) > 1e-10:
            hints.append(
                {
                    "asset": asset,
                    "exchange_qty": ex,
                    "internal_open_qty": inn,
                    "delta": d,
                }
            )
    return {"mismatch_hints": hints, "count": len(hints)}
