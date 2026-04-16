"""
Trade advisor: Claude + GPT review candidates before execution.
Brief, token-efficient. Returns ranked top 10–20 picks.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def _brief_market_summary(candidates: List[Dict[str, Any]]) -> str:
    """Ultra-brief market summary for AI review (minimize tokens)."""
    lines = [
        f"DATE: {time.strftime('%Y-%m-%d %H:%M')}",
        f"CANDIDATES: {len(candidates)}",
        "",
        "TOP CANDIDATES (prob|side|ticker|profit_per_contract|ttr_min):",
    ]
    for i, c in enumerate(candidates[:20], 1):
        edge = 1.0 - float(c.get("prob", 0) or 0)
        ttr = float(c.get("ttr", 0) or 0)
        tick = str(c.get("ticker", "?"))
        lines.append(
            f"{i:2}. {float(c.get('prob', 0) or 0) * 100:.0f}%|"
            f"{c.get('side', '?')}|"
            f"{tick[-20:]}|"
            f"${edge:.3f}|"
            f"{int(ttr / 60)}min"
        )
    return "\n".join(lines)


def get_claude_review(
    candidates: List[Dict[str, Any]],
    balance: float,
    lessons_summary: str,
    platform: str = "kalshi",
) -> Dict[str, Any]:
    """Ask Claude to review and rank candidates. Returns top picks with reasoning."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {"approved": candidates[:10], "source": "no_claude"}

    try:
        import anthropic

        client = anthropic.Anthropic()

        market_summary = _brief_market_summary(candidates)

        prompt = f"""You are a trading advisor.
Balance: ${balance:.2f}
Platform: {platform}
Mission: $1,000,000 in 6 months

LESSONS (mandatory):
{lessons_summary[:500]}

CANDIDATES:
{market_summary}

TASK: Rank top 10 by expected value.
OUTPUT JSON only, no explanation:
{{"picks": [
  {{"rank": 1, "ticker": "...",
    "side": "yes/no",
    "reason": "10 words max",
    "confidence": 0.0-1.0}},
  ...
]}}"""

        response = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(text[start:end])
            picks = data.get("picks", [])
            logger.info(
                "Claude reviewed %d candidates → %d picks",
                len(candidates),
                len(picks),
            )

            ticker_map = {c["ticker"]: c for c in candidates if c.get("ticker")}
            approved: List[Dict[str, Any]] = []
            for pick in picks:
                t = str(pick.get("ticker", ""))
                if t in ticker_map:
                    candidate = dict(ticker_map[t])
                    candidate["claude_rank"] = pick.get("rank", 99)
                    candidate["claude_reason"] = pick.get("reason", "")
                    candidate["claude_confidence"] = pick.get("confidence", 0)
                    approved.append(candidate)

            return {
                "approved": approved,
                "source": "claude",
                "picks_count": len(approved),
            }
    except Exception as e:
        logger.warning("Claude review failed: %s", e)

    return {"approved": candidates[:10], "source": "fallback"}


def get_gpt_research(
    candidates: List[Dict[str, Any]],
    balance: float,
) -> Dict[str, Any]:
    """Ask GPT to cross-check picks. Returns top tickers and avoid list."""
    if not os.environ.get("OPENAI_API_KEY"):
        return {"boosted": [], "source": "no_gpt", "top5": [], "avoid": []}

    try:
        import openai

        client = openai.OpenAI()

        summary = _brief_market_summary(candidates[:10])

        prompt = f"""Trading advisor check.
Balance: ${balance:.2f}

CANDIDATES:
{summary}

Which 5 have highest win probability?
JSON only:
{{"top5": ["ticker1","ticker2","ticker3",
           "ticker4","ticker5"],
  "avoid": ["ticker_if_any"]}}"""

        response = client.chat.completions.create(
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.choices[0].message.content
        if not text:
            raise ValueError("empty GPT response")
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(text[start:end])
            top5 = data.get("top5", [])
            avoid = data.get("avoid", [])
            logger.info("GPT top5: %s avoid: %s", top5, avoid)
            return {
                "top5": top5,
                "avoid": avoid,
                "source": "gpt",
            }
    except Exception as e:
        logger.warning("GPT review failed: %s", e)

    return {"top5": [], "avoid": [], "source": "fallback"}


def get_combined_review(
    candidates: List[Dict[str, Any]],
    balance: float,
    platform: str = "kalshi",
) -> List[Dict[str, Any]]:
    """
    Claude + GPT review candidates. Returns ranked list of best picks.
    """
    if not candidates:
        return []

    lessons_summary = ""
    try:
        from trading_ai.shark.lessons import load_lessons

        lessons = load_lessons()
        rules = lessons.get("rules", [])
        dnr = lessons.get("do_not_repeat", [])
        lessons_summary = (
            "RULES: " + "; ".join(rules[:5]) + "\nNEVER: " + "; ".join(dnr[:5])
        )
    except Exception:
        pass

    claude_result = get_claude_review(candidates, balance, lessons_summary, platform)
    approved = claude_result.get("approved", candidates[:10])

    gpt_result = get_gpt_research(approved, balance)
    top5 = gpt_result.get("top5", [])
    avoid = gpt_result.get("avoid", [])

    final: List[Dict[str, Any]] = []
    for c in approved:
        t = str(c.get("ticker", ""))
        if t in avoid:
            logger.info("GPT flagged avoid: %s", t)
            continue
        if t in top5:
            c["gpt_boosted"] = True
            c["combined_rank"] = int(c.get("claude_rank", 5)) - 2
        else:
            c["gpt_boosted"] = False
            c["combined_rank"] = int(c.get("claude_rank", 10))
        final.append(c)

    final.sort(key=lambda x: x.get("combined_rank", 99))

    logger.info(
        "Combined review: %d final picks (claude=%s gpt=%s)",
        len(final),
        claude_result.get("source"),
        gpt_result.get("source"),
    )

    return final[:20]
