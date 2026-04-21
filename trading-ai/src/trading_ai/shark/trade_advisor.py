"""
Trade advisor: Claude + GPT review candidates before execution.
Kalshi simple scan: dual confirmation — bet wins + numbers check (JSON), both models must approve.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Tuple

from trading_ai.llm.anthropic_defaults import DEFAULT_ANTHROPIC_MESSAGES_MODEL

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
            model=os.environ.get("ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MESSAGES_MODEL),
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


def _kalshi_dual_prompt(candidate: Dict[str, Any], balance: float) -> str:
    title = str(candidate.get("title") or candidate.get("ticker") or "")
    ticker = str(candidate.get("ticker") or "")
    side = str(candidate.get("side") or "yes").lower()
    prob = float(candidate.get("prob") or 0.0)
    ttr_min = max(0, int(float(candidate.get("ttr") or 0.0) / 60.0))
    px = max(float(candidate.get("price") or 0.01), 0.01)
    notional = max(5.0, min(float(balance) * 0.1, 250.0))
    contracts = max(1, int(notional / px))
    total_cost = contracts * px
    max_payout = float(contracts) * 1.0

    price_line = ""
    try:
        from trading_ai.shark.outlets.coinbase import CoinbaseClient

        if "BTC" in ticker.upper():
            p = CoinbaseClient().get_prices(["BTC-USD"])
            btc = p.get("BTC-USD", (0, 0))[0]
            if btc:
                price_line = f"Current BTC: ${btc:,.0f}"
        elif "ETH" in ticker.upper():
            p = CoinbaseClient().get_prices(["ETH-USD"])
            eth = p.get("ETH-USD", (0, 0))[0]
            if eth:
                price_line = f"Current ETH: ${eth:,.2f}"
    except Exception:
        pass

    return f"""Kalshi trade check.

MARKET: {title}
{price_line}
BET: {side.upper()} side
TIME LEFT: {ttr_min} minutes
PROBABILITY: {prob * 100:.0f}%

NUMBERS:
Contracts: {contracts}
Cost per contract: ${total_cost / max(contracts, 1):.3f}
Total cost: ${total_cost:.2f}
Max payout: ${max_payout:.2f}
Profit if win: ${max_payout - total_cost:.2f}

Answer these TWO questions only:
1. Will the {side.upper()} bet win?
   (Based on title + time + current prices)
2. Are the numbers correct?
   (payout > cost, makes sense)

JSON only:
{{"bet_will_win": true/false,
  "numbers_correct": true/false,
  "approve": true/false,
  "reason": "max 8 words"}}

approve = true only if BOTH are true.
If any doubt → approve = false."""


def confirm_kalshi_trade_dual_llm(
    candidate: Dict[str, Any],
    balance: float,
) -> Tuple[bool, str]:
    """
    Ask Claude and GPT two things only: bet wins, numbers check.
    Both must return approve=true to proceed.
    """
    prompt = _kalshi_dual_prompt(candidate, balance)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False, "No Claude key"
    try:
        import anthropic

        c = anthropic.Anthropic()
        resp = c.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MESSAGES_MODEL),
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text
        s = text.find("{")
        e = text.rfind("}") + 1
        if s < 0:
            return False, "Claude: no JSON"
        data = json.loads(text[s:e])
        claude_ok = bool(data.get("approve", False))
        reason = str(data.get("reason", ""))
        logger.info(
            "Claude Kalshi check: ok=%s bet_win=%s nums=%s | %s",
            claude_ok,
            data.get("bet_will_win"),
            data.get("numbers_correct"),
            reason,
        )
        if not claude_ok:
            return False, f"Claude: {reason}"
    except Exception as exc:
        logger.debug("Claude Kalshi check: %s", exc)
        return False, "Claude error"

    if not os.environ.get("OPENAI_API_KEY"):
        return False, "No GPT key"
    try:
        import openai

        g = openai.OpenAI()
        resp = g.chat.completions.create(
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.choices[0].message.content
        if not text:
            return False, "GPT empty"
        s = text.find("{")
        e = text.rfind("}") + 1
        if s < 0:
            return False, "GPT: no JSON"
        data = json.loads(text[s:e])
        gpt_ok = bool(data.get("approve", False))
        gpt_reason = str(data.get("reason", ""))
        logger.info(
            "GPT Kalshi check: ok=%s bet_win=%s nums=%s | %s",
            gpt_ok,
            data.get("bet_will_win"),
            data.get("numbers_correct"),
            gpt_reason,
        )
        if not gpt_ok:
            return False, f"GPT: {gpt_reason}"
    except Exception as exc:
        logger.debug("GPT Kalshi check: %s", exc)
        return False, "GPT error"

    return True, "dual_ok"


def get_combined_review(
    candidates: List[Dict[str, Any]],
    balance: float,
    platform: str = "kalshi",
) -> List[Dict[str, Any]]:
    """
    Kalshi: sequential dual LLM approval per candidate (first N).
    If either API key is missing, returns candidates unchanged (passthrough).
    """
    _ = platform
    if not candidates:
        return []

    if not (os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("OPENAI_API_KEY")):
        logger.warning(
            "Kalshi dual AI: missing ANTHROPIC or OPENAI key; passing candidates through",
        )
        return candidates[:20]

    out: List[Dict[str, Any]] = []
    for c in candidates[:20]:
        ok, reason = confirm_kalshi_trade_dual_llm(c, balance)
        if ok:
            out.append(c)
        else:
            logger.info(
                "Kalshi dual AI rejected %s: %s",
                c.get("ticker"),
                reason,
            )
    logger.info("Kalshi dual AI: %d / %d approved", len(out), min(20, len(candidates)))
    return out
