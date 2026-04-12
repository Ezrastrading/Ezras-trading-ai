from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from trading_ai.config import Settings
from trading_ai.models.schemas import TradeBrief

logger = logging.getLogger(__name__)


def format_brief_telegram(brief: TradeBrief) -> str:
    # Plain text avoids Telegram Markdown parse failures on user content.
    lines = [
        f"Signal {brief.signal_score}/10 — {brief.market_id}",
        f"Q: {brief.market_question[:400]}",
        f"Implied p: {brief.implied_probability}",
        "",
        "Supporting:",
        *[f"- {s[:300]}" for s in brief.supporting_evidence[:5]],
        "",
        "Opposing:",
        *[f"- {s[:300]}" for s in brief.opposing_evidence[:5]],
        "",
        "Drivers:",
        *[f"- {s[:300]}" for s in brief.probability_drivers[:5]],
        "",
        f"Uncertainty: {brief.uncertainty[:500]}",
        f"Edge: {brief.edge_hypothesis[:500]}",
    ]
    return "\n".join(lines)


def send_telegram_alert(settings: Settings, text: str) -> bool:
    token = settings.telegram_bot_token
    chat = settings.telegram_chat_id
    if not token or not chat:
        logger.warning("Telegram not configured; skipping alert")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat,
        "text": text[:4000],
        "disable_web_page_preview": True,
    }
    with httpx.Client(timeout=30.0) as client:
        r = client.post(url, json=payload)
        r.raise_for_status()
    return True


def send_trade_brief_alert(settings: Settings, brief: TradeBrief) -> tuple[bool, datetime]:
    text = format_brief_telegram(brief)
    sent_at = datetime.now(timezone.utc)
    ok = send_telegram_alert(settings, text)
    return ok, sent_at
