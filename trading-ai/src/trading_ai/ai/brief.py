from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import httpx

from trading_ai.config import Settings
from trading_ai.models.schemas import CandidateMarket, EnrichmentBundle, TradeBrief

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a disciplined prediction-market analyst.
Return ONLY valid JSON matching this shape (no markdown):
{
  "implied_probability": number or null,
  "supporting_evidence": [string],
  "opposing_evidence": [string],
  "probability_drivers": [string],
  "uncertainty": string,
  "edge_hypothesis": string,
  "signal_score": integer 1-10
}
signal_score: higher only when evidence is specific, timely, and actionable for this market."""


def _context_block(market: CandidateMarket, bundle: EnrichmentBundle) -> str:
    lines = [
        f"Market: {market.question}",
        f"Reported implied (platform): {market.implied_probability}",
        f"Volume USD: {market.volume_usd}",
        f"End: {market.end_date_iso}",
        "",
        "Sources (Tavily):",
    ]
    for s in bundle.tavily_results:
        lines.append(f"- {s.url} ({s.title or ''}) @ {s.fetched_at.isoformat()}")
    lines.append("Firecrawl pages:")
    for s in bundle.firecrawl_results:
        lines.append(f"- {s.url}")
    if bundle.gpt_researcher_notes:
        lines.append("GPT Researcher notes:")
        lines.append(bundle.gpt_researcher_notes[:8000])
    return "\n".join(lines)


def generate_trade_brief(
    settings: Settings,
    market: CandidateMarket,
    bundle: EnrichmentBundle,
) -> TradeBrief:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required for trade briefs")
    user = _context_block(market, bundle)
    url = f"{settings.openai_base.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": settings.openai_model,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
    }
    with httpx.Client(timeout=120.0) as client:
        r = client.post(url, headers=headers, json=body)
        r.raise_for_status()
        data = r.json()
    content = data["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    implied = parsed.get("implied_probability")
    if implied is not None:
        try:
            implied_f = float(implied)
            implied_f = max(0.0, min(1.0, implied_f))
        except (TypeError, ValueError):
            implied_f = market.implied_probability
    else:
        implied_f = market.implied_probability
    score = int(parsed.get("signal_score", 5))
    score = max(1, min(10, score))
    return TradeBrief(
        market_id=market.market_id,
        market_question=market.question,
        implied_probability=implied_f,
        supporting_evidence=list(parsed.get("supporting_evidence") or []),
        opposing_evidence=list(parsed.get("opposing_evidence") or []),
        probability_drivers=list(parsed.get("probability_drivers") or []),
        uncertainty=str(parsed.get("uncertainty") or ""),
        edge_hypothesis=str(parsed.get("edge_hypothesis") or ""),
        signal_score=score,
        created_at=datetime.now(timezone.utc),
        model=settings.openai_model,
    )
