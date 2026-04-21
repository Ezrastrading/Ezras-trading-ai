"""Metaculus — prediction-market intelligence (no execution; cross-check vs Kalshi)."""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from trading_ai.shark.dotenv_load import load_shark_dotenv
from trading_ai.shark.models import MarketSnapshot
from trading_ai.shark.outlets.base import BaseOutletFetcher

load_shark_dotenv()

logger = logging.getLogger(__name__)

_METACULUS_BASE = "https://www.metaculus.com/api2"


def _http_get_json(url: str, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={**(headers or {}), "User-Agent": "EzrasShark/1.0", "Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _community_yes_probability(row: Dict[str, Any]) -> Optional[float]:
    cp = row.get("community_prediction")
    if cp is None:
        return None
    if isinstance(cp, (int, float)):
        v = float(cp)
        return v / 100.0 if v > 1.0 else v
    if not isinstance(cp, dict):
        return None
    full = cp.get("full") if isinstance(cp.get("full"), dict) else cp
    if not isinstance(full, dict):
        return None
    for key in ("q2", "median", "mean"):
        if key in full and full[key] is not None:
            v = float(full[key])
            return v / 100.0 if v > 1.0 else max(0.0, min(1.0, v))
    return None


def _resolve_unix(row: Dict[str, Any]) -> Optional[float]:
    for k in ("scheduled_close_time", "resolve_time", "close_time", "resolution_time"):
        v = row.get(k)
        if v is None:
            continue
        if isinstance(v, (int, float)):
            ts = float(v)
            if ts > 1e12:
                return ts / 1000.0
            return ts
        if isinstance(v, str):
            try:
                from datetime import datetime

                if v.endswith("Z"):
                    v = v[:-1] + "+00:00"
                return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
            except Exception:
                continue
    return None


def map_metaculus_question_to_snapshot(row: Dict[str, Any], now: float) -> Optional[MarketSnapshot]:
    qid = row.get("id")
    if qid is None:
        return None
    title = str(row.get("title") or row.get("short_title") or "").strip()
    crit = str(row.get("resolution_criteria") or row.get("description") or title or "")
    yes = _community_yes_probability(row)
    if yes is None:
        return None
    yes = max(0.01, min(0.99, float(yes)))
    no = max(0.01, min(0.99, 1.0 - yes))
    end = _resolve_unix(row)
    ttr = max(0.0, float(end) - now) if end is not None else 86400.0 * 30
    ncast = int(row.get("number_of_forecasters") or row.get("comment_count") or 0)
    vol_proxy = float(ncast) * 10.0
    return MarketSnapshot(
        market_id=f"metaculus:{qid}",
        outlet="metaculus",
        yes_price=yes,
        no_price=no,
        volume_24h=vol_proxy,
        time_to_resolution_seconds=ttr,
        resolution_criteria=crit[:4000] or title,
        last_price_update_timestamp=now,
        underlying_data_if_available={"metaculus_raw": row},
        market_category="metaculus",
        question_text=title or None,
        end_timestamp_unix=end,
        end_date_seconds=end,
    )


class MetaculusFetcher(BaseOutletFetcher):
    outlet_name = "metaculus"

    def fetch_binary_markets(self) -> List[MarketSnapshot]:
        """
        Open binary questions ordered by activity.
        Metaculus now requires an API token — set ``METACULUS_API_TOKEN`` (header: ``Token <token>``).
        """
        if (os.environ.get("METACULUS_FETCH_ENABLED") or "true").strip().lower() not in ("1", "true", "yes"):
            return []
        token = (os.environ.get("METACULUS_API_TOKEN") or "").strip()
        if not token:
            logger.debug("Metaculus: METACULUS_API_TOKEN not set — skipping fetch (intelligence disabled)")
            return []
        params = urllib.parse.urlencode(
            {
                "status": "open",
                "type": "binary",
                "order_by": "activity",
                "limit": min(100, int((os.environ.get("METACULUS_FETCH_LIMIT") or "100").strip() or "100")),
            }
        )
        url = f"{_METACULUS_BASE}/questions/?{params}"
        headers = {"Authorization": f"Token {token}"}
        try:
            body = _http_get_json(url, headers=headers)
        except urllib.error.HTTPError as e:
            logger.warning("Metaculus HTTP %s: %s", e.code, e.reason)
            return []
        except Exception as exc:
            logger.warning("Metaculus fetch failed: %s", exc)
            return []
        results = body.get("results")
        if not isinstance(results, list):
            results = body.get("data") if isinstance(body.get("data"), list) else []
        now = time.time()
        out: List[MarketSnapshot] = []
        for row in results:
            if not isinstance(row, dict):
                continue
            snap = map_metaculus_question_to_snapshot(row, now)
            if snap:
                out.append(snap)
        logger.info("Metaculus: %s binary snapshots", len(out))
        return out
