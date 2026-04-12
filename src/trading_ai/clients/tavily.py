from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List

import httpx

from trading_ai.config import Settings
from trading_ai.models.schemas import SourceRef

logger = logging.getLogger(__name__)


def tavily_search(settings: Settings, query: str, max_results: int = 5) -> List[SourceRef]:
    if not settings.tavily_api_key:
        logger.debug("Tavily skipped: no TAVILY_API_KEY")
        return []
    url = f"{settings.tavily_base.rstrip('/')}/search"
    body = {
        "api_key": settings.tavily_api_key,
        "query": query,
        "max_results": max_results,
        "include_answer": False,
    }
    now = datetime.now(timezone.utc)
    with httpx.Client(timeout=45.0) as client:
        r = client.post(url, json=body)
        r.raise_for_status()
        data = r.json()
    results = data.get("results") or []
    out: List[SourceRef] = []
    for item in results:
        u = item.get("url")
        if not u:
            continue
        out.append(
            SourceRef(
                url=str(u),
                title=item.get("title"),
                fetched_at=now,
                provider="tavily",
            )
        )
    return out
