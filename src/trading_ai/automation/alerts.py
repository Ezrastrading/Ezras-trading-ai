from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

import httpx

from trading_ai.config import Settings
from trading_ai.models.schemas import TradeBrief

logger = logging.getLogger(__name__)

_MAX_LINE = 140
_MAX_TITLE = 200


def _pct(implied: Optional[float]) -> str:
    if implied is None:
        return "n/a"
    try:
        return f"{float(implied) * 100:.0f}%"
    except (TypeError, ValueError):
        return "n/a"


def _one_line(items: List[str], fallback: str = "—") -> str:
    if not items:
        return fallback
    text = (items[0] or "").strip().replace("\n", " ")
    if len(text) > _MAX_LINE:
        return text[: _MAX_LINE - 1] + "…"
    return text or fallback


def _run_display(run_id: str) -> str:
    if len(run_id) <= 12:
        return run_id
    return f"{run_id[:8]}…"


def format_brief_telegram(
    brief: TradeBrief,
    *,
    run_id: str,
    source_urls: List[str],
) -> str:
    """Concise, mobile-friendly plain text (no Markdown)."""
    title = (brief.market_question or "").strip()
    if len(title) > _MAX_TITLE:
        title = title[: _MAX_TITLE - 1] + "…"

    lines = [
        "Trading AI — signal alert",
        "",
        f"Run: {_run_display(run_id)}",
        "",
        title,
        "",
        f"p: {_pct(brief.implied_probability)}  ·  score {brief.signal_score}/10",
        "",
        f"Bull: {_one_line(brief.supporting_evidence)}",
        f"Bear: {_one_line(brief.opposing_evidence)}",
    ]

    urls = [u.strip() for u in source_urls[:2] if u and u.strip()]
    if urls:
        lines.extend(["", "Sources:"])
        for i, u in enumerate(urls, start=1):
            display = u if len(u) <= 72 else u[:69] + "…"
            lines.append(f"{i}) {display}")

    text = "\n".join(lines)
    return text[:4000]


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


def send_trade_brief_alert(
    settings: Settings,
    brief: TradeBrief,
    *,
    run_id: str,
    source_urls: List[str],
) -> tuple[bool, datetime]:
    text = format_brief_telegram(brief, run_id=run_id, source_urls=source_urls)
    sent_at = datetime.now(timezone.utc)
    ok = send_telegram_alert(settings, text)
    return ok, sent_at
