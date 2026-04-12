from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from trading_ai.config import Settings
from trading_ai.models.schemas import SourceRef

logger = logging.getLogger(__name__)


def firecrawl_scrape(settings: Settings, url: str) -> Optional[SourceRef]:
    if not settings.firecrawl_api_key:
        logger.debug("Firecrawl skipped: no FIRECRAWL_API_KEY")
        return None
    endpoint = f"{settings.firecrawl_base.rstrip('/')}/v1/scrape"
    body = {"url": url, "formats": ["markdown"]}
    now = datetime.now(timezone.utc)
    headers = {
        "Authorization": f"Bearer {settings.firecrawl_api_key}",
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.post(endpoint, json=body, headers=headers)
    except httpx.RequestError as exc:
        logger.warning("Firecrawl request failed for url=%s: %s", url, exc)
        return None

    if r.status_code >= 400:
        logger.warning(
            "Firecrawl HTTP %s for url=%s (skipping this page; enrichment continues)",
            r.status_code,
            url,
        )
        return None

    try:
        data = r.json()
    except ValueError as exc:
        logger.warning("Firecrawl invalid JSON for url=%s: %s", url, exc)
        return None

    meta = data.get("data") or data
    title = None
    if isinstance(meta, dict):
        title = meta.get("metadata", {}).get("title") if isinstance(meta.get("metadata"), dict) else meta.get("title")
    return SourceRef(url=url, title=title, fetched_at=now, provider="firecrawl")
