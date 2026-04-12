from __future__ import annotations

import sys

from trading_ai.config import get_settings


def _is_set(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def run_validation() -> int:
    """
    Exit 0 if core keys present; non-zero with message if critical gaps.
    Uses the same Settings loader as the pipeline (including `.env` from project root).
    """
    settings = get_settings()

    if not _is_set(settings.openai_api_key):
        print("CRITICAL missing: OPENAI_API_KEY", file=sys.stderr)
        print("Add it to trading-ai/.env (see .env.example).", file=sys.stderr)
        return 2

    optional_warn = []
    if not _is_set(settings.tavily_api_key):
        optional_warn.append("TAVILY_API_KEY")
    if not _is_set(settings.firecrawl_api_key):
        optional_warn.append("FIRECRAWL_API_KEY")
    if not _is_set(settings.telegram_bot_token):
        optional_warn.append("TELEGRAM_BOT_TOKEN")
    if not _is_set(settings.telegram_chat_id):
        optional_warn.append("TELEGRAM_CHAT_ID")

    if optional_warn:
        print("Optional unset (some features disabled):", ", ".join(optional_warn))

    print("Environment OK for Phase 1 pipeline (OpenAI present).")
    return 0
