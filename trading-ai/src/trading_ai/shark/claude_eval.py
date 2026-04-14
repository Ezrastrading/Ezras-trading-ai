"""Claude trade evaluator — optional gate before venue execution (requires ANTHROPIC_API_KEY)."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from trading_ai.shark.dotenv_load import load_shark_dotenv

if TYPE_CHECKING:
    from trading_ai.shark.models import ExecutionIntent, ScoredOpportunity

load_shark_dotenv()
logger = logging.getLogger(__name__)


def _normalize_result(raw: Dict[str, Any]) -> Dict[str, Any]:
    d = str(raw.get("decision", "SKIP")).strip().upper()
    if d not in ("YES", "NO", "SKIP"):
        d = "SKIP"
    try:
        tp = float(raw.get("true_probability", 0.5))
    except (TypeError, ValueError):
        tp = 0.5
    tp = max(0.0, min(1.0, tp))
    try:
        conf = float(raw.get("confidence", 0.5))
    except (TypeError, ValueError):
        conf = 0.5
    conf = max(0.0, min(1.0, conf))
    try:
        sm = float(raw.get("size_multiplier", 1.0))
    except (TypeError, ValueError):
        sm = 1.0
    sm = max(0.5, min(2.0, sm))
    reason = str(raw.get("reasoning", "") or "")[:800]
    return {
        "decision": d,
        "true_probability": tp,
        "confidence": conf,
        "size_multiplier": sm,
        "reasoning": reason,
    }


def claude_evaluate_trade(
    market_question: str,
    market_platform: str,
    yes_price: float,
    no_price: float,
    hunt_type: str,
    hunt_edge: float,
    hunt_side: str,
    btc_price: Optional[float] = None,
    minutes_to_resolve: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic not installed; Claude evaluator unavailable")
        return None
    api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        return None
    model = (os.environ.get("ANTHROPIC_MODEL") or "claude-sonnet-4-20250514").strip()
    client = anthropic.Anthropic(api_key=api_key)
    btc_line = f"BTC price: ${btc_price:.2f}\n" if btc_price is not None else ""
    res_line = (
        f"Minutes to resolve: {minutes_to_resolve:.1f}\n" if minutes_to_resolve is not None else ""
    )
    prompt = f"""You are an expert prediction market trader evaluating a potential trade.

MARKET: {market_question}
PLATFORM: {market_platform}
YES price: {yes_price:.3f}
NO price: {no_price:.3f}
Hunt type: {hunt_type}
Detected edge: {hunt_edge:.3f}
Suggested side: {hunt_side}
{btc_line}{res_line}
Evaluate this trade opportunity.
Consider:
1. Is the detected edge real or noise?
2. Is the suggested side correct?
3. What is the true probability?
4. Should we trade YES, NO, or SKIP?
5. What size (use size_multiplier 0.5-2.0 relative to base; 1.0 = default)?

Respond ONLY in JSON:
{{
  "decision": "YES" | "NO" | "SKIP",
  "true_probability": 0.0,
  "confidence": 0.0,
  "size_multiplier": 1.0,
  "reasoning": "one sentence"
}}"""

    response = client.messages.create(
        model=model,
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            logger.warning("Claude response not JSON: %s", text[:200])
            return None
        raw = json.loads(m.group())
    if not isinstance(raw, dict):
        return None
    out = _normalize_result(raw)
    logger.info(
        "Claude eval raw decision=%s confidence=%.3f",
        out["decision"],
        out["confidence"],
    )
    return out


def apply_claude_evaluator_gate(
    scored: "ScoredOpportunity",
    intent: "ExecutionIntent",
    *,
    capital: float,
) -> Tuple[bool, str]:
    """
    When ANTHROPIC_API_KEY is set and scored.score > 0.25, call Claude and possibly
    adjust ``intent`` (side, stake, meta). Returns (proceed, halt_reason).
    If proceed is False, halt_reason is ``claude_skip``.
    """
    from trading_ai.shark.executor import MIN_POSITION_USD

    if not (os.environ.get("ANTHROPIC_API_KEY") or "").strip():
        return True, ""
    if scored.score <= 0.25:
        return True, ""

    m = scored.market
    q = (getattr(m, "question_text", None) or m.resolution_criteria or str(m.market_id))[:4000]
    hunt_label = ",".join(h.value for h in intent.hunt_types) or "unknown"
    mins = (m.time_to_resolution_seconds or 0.0) / 60.0
    btc = intent.meta.get("btc_price")
    btc_f = float(btc) if btc is not None else None

    try:
        ev = claude_evaluate_trade(
            market_question=q,
            market_platform=intent.outlet,
            yes_price=float(m.yes_price),
            no_price=float(m.no_price),
            hunt_type=hunt_label,
            hunt_edge=float(intent.edge_after_fees),
            hunt_side=str(intent.side).upper(),
            btc_price=btc_f,
            minutes_to_resolve=mins if mins > 0 else None,
        )
    except Exception as exc:
        logger.warning("Claude evaluator failed (proceeding without gate): %s", exc)
        return True, ""

    if not ev:
        return True, ""

    logger.info(
        "Claude eval: decision=%s prob=%.3f confidence=%.3f reasoning=%s",
        ev["decision"],
        ev["true_probability"],
        ev["confidence"],
        ev["reasoning"][:200],
    )

    intent.meta["claude_reasoning"] = ev["reasoning"]
    intent.meta["claude_confidence"] = ev["confidence"]
    intent.meta["claude_true_probability"] = ev["true_probability"]
    intent.meta["claude_decision"] = ev["decision"]

    if ev["decision"] == "SKIP":
        return False, "claude_skip"

    if ev["decision"] in ("YES", "NO"):
        new_side = ev["decision"].lower()
        if new_side != intent.side:
            logger.info("Claude overrides side: %s → %s", intent.side, new_side)
            intent.side = new_side
            exp = m.yes_price if intent.side == "yes" else m.no_price
            intent.expected_price = float(exp)

    sm = float(ev["size_multiplier"])
    intent.stake_fraction_of_capital *= sm
    cap = max(float(capital), 1e-9)
    intent.stake_fraction_of_capital = min(intent.stake_fraction_of_capital, 1.0)
    intent.notional_usd = max(0.0, cap * intent.stake_fraction_of_capital)
    if 0.0 < intent.notional_usd < MIN_POSITION_USD:
        intent.notional_usd = MIN_POSITION_USD
        intent.stake_fraction_of_capital = min(intent.notional_usd / cap, 1.0)
    px = max(intent.expected_price, 1e-6)
    intent.shares = max(1, int(intent.notional_usd / px))

    return True, ""
