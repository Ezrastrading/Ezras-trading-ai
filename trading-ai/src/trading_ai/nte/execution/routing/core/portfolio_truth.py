"""Portfolio-wide balances and USD marks (venue hooks via adapter dicts)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from trading_ai.shark.outlets.coinbase import CoinbaseClient


@dataclass
class AssetBalanceRow:
    currency: str
    available: float
    mark_usd: Optional[float] = None
    dust: bool = False


@dataclass
class PortfolioTruthSnapshot:
    rows: List[AssetBalanceRow] = field(default_factory=list)
    total_marked_usd: float = 0.0
    liquid_quote_usd: float = 0.0
    notes: List[str] = field(default_factory=list)


def _usd_mark_for_crypto(
    product_id_guess: str,
    qty: float,
    *,
    price_fetch: Any,
) -> Optional[float]:
    if qty <= 0:
        return 0.0
    px = price_fetch(product_id_guess)
    if px is None or px <= 0:
        return None
    return qty * float(px)


def build_portfolio_truth_coinbase(
    client: CoinbaseClient,
    *,
    dust_threshold_usd: float = 1.0,
) -> PortfolioTruthSnapshot:
    """
    All wallet currencies with best-effort USD mark via ``*-USD`` ticker when available.
    """
    from trading_ai.shark.outlets import coinbase as cb_mod

    def px_usd(base: str) -> Optional[float]:
        pid = f"{base.upper()}-USD"
        try:
            j = cb_mod._brokerage_public_request(f"/market/products/{pid}/ticker")
            if not isinstance(j, dict):
                return None
            for k in ("price", "best_bid", "best_ask"):
                v = j.get(k)
                if v is not None:
                    return float(v)
        except Exception:
            return None
        return None

    rows: List[AssetBalanceRow] = []
    total = 0.0
    liq_quote = 0.0
    notes: List[str] = []

    for a in client.list_all_accounts():
        if not isinstance(a, dict):
            continue
        cur = str(a.get("currency") or "").upper()
        if not cur:
            continue
        avail = CoinbaseClient._account_usd_usdc_spendable(a)
        mark: Optional[float] = None
        if cur in ("USD", "USDC"):
            mark = avail
            liq_quote += avail if cur == "USD" else avail
        else:
            mark = _usd_mark_for_crypto(cur, avail, price_fetch=px_usd)
            if mark is None:
                notes.append(f"no_usd_ticker_mark_for_{cur}")
        dust = (mark or 0.0) < dust_threshold_usd and avail > 0
        rows.append(AssetBalanceRow(currency=cur, available=avail, mark_usd=mark, dust=dust))
        total += mark or 0.0

    return PortfolioTruthSnapshot(
        rows=sorted(rows, key=lambda r: -(r.mark_usd or 0.0)),
        total_marked_usd=total,
        liquid_quote_usd=liq_quote,
        notes=notes,
    )


def deployable_quote_usd_proxy(rows: Mapping[str, float]) -> float:
    """From simple quote map USD+USDC (USDC≈1)."""
    return float(rows.get("USD", 0.0)) + float(rows.get("USDC", 0.0))
