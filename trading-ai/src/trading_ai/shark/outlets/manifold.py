"""Manifold — https://api.manifold.markets (public reads)."""

from __future__ import annotations

import os
import time
from typing import List

from trading_ai.shark.dotenv_load import load_shark_dotenv
from trading_ai.shark.models import MarketSnapshot
from trading_ai.shark.outlets.base import BaseOutletFetcher

load_shark_dotenv()


def require_manifold_credentials_for_live() -> str:
    """API key for authenticated calls (e.g. bets); raises if unset."""
    from trading_ai.shark.required_env import require_manifold_api_key

    return require_manifold_api_key()


class ManifoldFetcher(BaseOutletFetcher):
    outlet_name = "manifold"
    API = os.environ.get("MANIFOLD_API_BASE", "https://api.manifold.markets/v0")

    def fetch_binary_markets(self) -> List[MarketSnapshot]:
        import time as _t

        now = _t.time()
        try:
            raw = self.http_get_json(f"{self.API}/markets?limit=30")
        except Exception:
            return []
        out: List[MarketSnapshot] = []
        rows = raw if isinstance(raw, list) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("outcomeType") not in (None, "BINARY", "MULTIPLE_CHOICE"):
                continue
            mid = str(row.get("id") or "")
            if not mid:
                continue
            try:
                p = float(row.get("probability") or 0.5)
            except (TypeError, ValueError):
                p = 0.5
            ttr = 86400.0
            ct = row.get("closeTime")
            if ct:
                try:
                    close_s = float(ct) / 1000.0
                    ttr = max(3600.0, close_s - now)
                except (TypeError, ValueError):
                    pass
            out.append(
                MarketSnapshot(
                    market_id=f"manifold:{mid}",
                    outlet=self.outlet_name,
                    yes_price=p,
                    no_price=1.0 - p,
                    volume_24h=float(row.get("volume24Hours") or row.get("volume") or 0),
                    time_to_resolution_seconds=ttr,
                    resolution_criteria=str(row.get("question") or ""),
                    last_price_update_timestamp=now,
                )
            )
        return out
