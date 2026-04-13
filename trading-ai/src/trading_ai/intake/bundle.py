from __future__ import annotations

import logging
from typing import List, Optional

from trading_ai.clients.firecrawl import firecrawl_scrape
from trading_ai.clients.tavily import tavily_search
from trading_ai.config import Settings
from trading_ai.intake.gpt_researcher_hooks import run_gpt_researcher_hook
from trading_ai.models.schemas import CandidateMarket, EnrichmentBundle, SourceRef

logger = logging.getLogger(__name__)


def _has_usable_evidence(
    tavily: List[SourceRef],
    firecrawl_results: List[SourceRef],
    gpt_notes: Optional[str],
) -> bool:
    if tavily:
        return True
    if firecrawl_results:
        return True
    if gpt_notes and gpt_notes.strip():
        return True
    return False


def enrich_market(settings: Settings, market: CandidateMarket) -> EnrichmentBundle:
    query = market.question.strip() or (market.slug or market.market_id)
    tavily = tavily_search(settings, query)
    firecrawl_results: List[SourceRef] = []
    for ref in tavily[:2]:
        scraped = firecrawl_scrape(settings, ref.url)
        if scraped:
            firecrawl_results.append(scraped)
    notes, gr_sources = run_gpt_researcher_hook(settings, query)

    if not _has_usable_evidence(tavily, firecrawl_results, notes):
        logger.warning(
            "Enrichment has no external sources for market_id=%s; brief will use market text only",
            market.market_id,
        )

    return EnrichmentBundle(
        market_id=market.market_id,
        query=query,
        tavily_results=tavily,
        firecrawl_results=firecrawl_results,
        gpt_researcher_notes=notes,
        gpt_researcher_sources=gr_sources,
    )
