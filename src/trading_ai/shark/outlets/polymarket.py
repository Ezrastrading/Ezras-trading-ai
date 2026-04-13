"""Polymarket CLOB — https://clob.polymarket.com (read-only markets list; wire wallet for orders)."""

from __future__ import annotations

import logging
import os
from typing import List

from trading_ai.shark.dotenv_load import load_shark_dotenv
from trading_ai.shark.models import MarketSnapshot
from trading_ai.shark.outlets.base import BaseOutletFetcher

load_shark_dotenv()

logger = logging.getLogger(__name__)


def require_polymarket_credentials_for_live() -> tuple[str, str]:
    """
    Return wallet + API key strings for live signing when set.
    Does not raise if unset — Polymarket may be scan-only (e.g. US operators).
    """
    load_shark_dotenv()
    w = (os.environ.get("POLY_WALLET_KEY") or "").strip()
    k = (os.environ.get("POLY_API_KEY") or "").strip()
    if not w:
        logger.warning("Polymarket in scan-only mode")
    if not k:
        logger.warning("Polymarket API key empty — public endpoints only for scanning")
    return w, k


class PolymarketFetcher(BaseOutletFetcher):
    outlet_name = "polymarket"
    CLOB_BASE = os.environ.get("POLY_CLOB_BASE", "https://clob.polymarket.com")

    def fetch_binary_markets(self) -> List[MarketSnapshot]:
        """
        Fetch simplified active markets. Full integration: markets + order book + EIP-712 signing
        via POLY_WALLET_KEY / POLY_API_KEY.
        """
        import time

        try:
            raw = self.http_get_json(f"{self.CLOB_BASE}/markets?limit=50")
        except Exception:
            return []
        now = time.time()
        out: List[MarketSnapshot] = []
        rows = raw if isinstance(raw, list) else raw.get("data") if isinstance(raw, dict) else []
        if not isinstance(rows, list):
            return []
        for row in rows[:30]:
            if not isinstance(row, dict):
                continue
            tid = str(row.get("condition_id") or row.get("id") or "")
            if not tid:
                continue
            try:
                yes = float(row.get("yes_price") or row.get("price") or 0.5)
                no = float(row.get("no_price") or (1.0 - yes))
            except (TypeError, ValueError):
                continue
            out.append(
                MarketSnapshot(
                    market_id=f"poly:{tid}",
                    outlet=self.outlet_name,
                    yes_price=yes,
                    no_price=no,
                    volume_24h=float(row.get("volume") or row.get("volume_24h") or 0),
                    time_to_resolution_seconds=float(row.get("time_to_resolution") or 86400.0),
                    resolution_criteria=str(row.get("description") or row.get("question") or ""),
                    last_price_update_timestamp=now,
                )
            )
        return out
