from __future__ import annotations

import os
import sys
from typing import Iterable


def _missing(keys: Iterable[str]) -> list[str]:
    return [k for k in keys if not os.environ.get(k)]


def run_validation() -> int:
    """
    Exit 0 if core keys present; non-zero with message if critical gaps.
    Loads .env via pydantic-settings when Settings is imported — run from project root.
    """
    from trading_ai.config import get_settings

    get_settings()  # loads .env from cwd

    critical = ["OPENAI_API_KEY"]
    missing_crit = _missing(critical)
    optional_warn = _missing(
        [
            "TAVILY_API_KEY",
            "FIRECRAWL_API_KEY",
            "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_CHAT_ID",
        ]
    )

    if missing_crit:
        print("CRITICAL missing:", ", ".join(missing_crit), file=sys.stderr)
        print("Add them to trading-ai/.env (see .env.example).", file=sys.stderr)
        return 2

    if optional_warn:
        print("Optional unset (some features disabled):", ", ".join(optional_warn))

    print("Environment OK for Phase 1 pipeline (OpenAI present).")
    return 0
