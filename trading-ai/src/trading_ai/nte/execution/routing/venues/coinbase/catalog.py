"""Coinbase Advanced Trade public SPOT catalog — real products only."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from trading_ai.nte.execution.routing.core.universal_types import UniversalProductEdge
from trading_ai.shark.outlets import coinbase as cb_mod

logger = logging.getLogger(__name__)


def fetch_coinbase_spot_products_online(*, max_pages: int = 40) -> List[Dict[str, Any]]:
    """
    Paginated ``GET /market/products?product_type=SPOT`` — **all** online spot rows (not USD-filtered).

    Each row includes ``product_id``, ``base_currency_id``, ``quote_currency_id`` when present.
    """
    out: List[Dict[str, Any]] = []
    limit = 500
    offset = 0
    pages = 0
    try:
        while pages < max_pages:
            path = f"/market/products?limit={limit}&offset={offset}&product_type=SPOT"
            j = cb_mod._brokerage_public_request(path)
            rows = j.get("products") or []
            if not isinstance(rows, list) or not rows:
                break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if str(row.get("product_type") or "").upper() != "SPOT":
                    continue
                if row.get("status") != "online":
                    continue
                if row.get("trading_disabled") or row.get("is_disabled"):
                    continue
                pid = str(row.get("product_id") or "").strip()
                if not pid:
                    continue
                out.append(row)
            if len(rows) < limit:
                break
            offset += limit
            pages += 1
    except Exception as exc:
        logger.warning("coinbase spot catalog fetch failed: %s", exc)
        return []
    return out


def rows_to_universal_edges(rows: List[Dict[str, Any]]) -> List[UniversalProductEdge]:
    edges: List[UniversalProductEdge] = []
    for row in rows:
        pid = str(row.get("product_id") or "").strip().upper()
        base = str(row.get("base_currency_id") or "").strip().upper()
        quote = str(row.get("quote_currency_id") or "").strip().upper()
        if not base or not quote:
            parts = pid.split("-")
            if len(parts) >= 2:
                base = base or parts[0]
                quote = quote or parts[-1]
        if not base or not quote:
            continue
        vol = row.get("approximate_quote_24h_volume")
        try:
            liq = float(vol) if vol is not None and str(vol).strip() != "" else 0.0
        except (TypeError, ValueError):
            liq = 0.0
        edges.append(
            UniversalProductEdge(
                venue="coinbase",
                product_id=pid,
                base_asset=base,
                quote_asset=quote,
                liquidity_proxy=max(1.0, liq),
                healthy=True,
                raw=row,
            )
        )
    return edges


def build_coinbase_spot_graph_edges() -> List[UniversalProductEdge]:
    return rows_to_universal_edges(fetch_coinbase_spot_products_online())
