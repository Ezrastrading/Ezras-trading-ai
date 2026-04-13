from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, List, Optional

import httpx

from trading_ai.config import Settings
from trading_ai.models.schemas import CandidateMarket

logger = logging.getLogger(__name__)


def _parse_json_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _first_implied_prob(market: dict[str, Any]) -> Optional[float]:
    """Derive a single implied probability from outcomePrices (first outcome = YES-ish)."""
    prices_raw = market.get("outcomePrices") or market.get("outcome_prices")
    outcomes = _parse_json_list(prices_raw)
    floats: List[float] = []
    for o in outcomes:
        try:
            floats.append(float(o))
        except (TypeError, ValueError):
            continue
    if not floats:
        return None
    return max(0.0, min(1.0, floats[0]))


def _volume_usd(market: dict[str, Any]) -> Optional[float]:
    for key in ("volumeNum", "volume", "liquidityNum", "liquidity"):
        v = market.get(key)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _market_id(market: dict[str, Any]) -> str:
    for key in ("id", "condition_id", "conditionId"):
        v = market.get(key)
        if v is not None:
            return str(v)
    slug = market.get("slug")
    if slug:
        return str(slug)
    return str(hash(json.dumps(market, sort_keys=True, default=str)))


def _days_to_expiry(end_date_iso: Optional[str]) -> Optional[float]:
    if not end_date_iso:
        return None
    try:
        raw = end_date_iso.replace("Z", "+00:00")
        end = datetime.fromisoformat(raw)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (end - now).total_seconds() / 86400.0
    except (ValueError, TypeError):
        return None


def fetch_markets(settings: Settings) -> List[dict[str, Any]]:
    url = settings.polymarket_gamma_base.rstrip("/") + settings.polymarket_markets_path
    params: dict[str, Any] = {
        "limit": settings.markets_fetch_limit,
        "active": "true",
        "closed": "false",
    }
    with httpx.Client(timeout=60.0) as client:
        r = client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "data" in data:
        inner = data["data"]
        return inner if isinstance(inner, list) else []
    logger.warning("Unexpected Polymarket response shape")
    return []


def to_candidate(market: dict[str, Any]) -> CandidateMarket:
    end = market.get("endDate") or market.get("end_date_iso") or market.get("endDateIso")
    end_s = str(end) if end is not None else None
    question = str(market.get("question") or market.get("title") or "")
    implied = _first_implied_prob(market)
    prices_raw = market.get("outcomePrices") or market.get("outcome_prices")
    outcomes = _parse_json_list(prices_raw)
    outcome_floats: List[float] = []
    for o in outcomes:
        try:
            outcome_floats.append(float(o))
        except (TypeError, ValueError):
            continue
    return CandidateMarket(
        market_id=_market_id(market),
        slug=market.get("slug"),
        question=question,
        volume_usd=_volume_usd(market),
        end_date_iso=end_s,
        days_to_expiry=_days_to_expiry(end_s),
        implied_probability=implied,
        outcome_prices=outcome_floats or None,
        raw=market,
    )
