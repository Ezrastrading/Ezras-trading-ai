"""Mid-session + end-of-day CEO questions — keep / scale / reduce / test / cut."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict

logger = logging.getLogger(__name__)


def run_twice_daily_session(
    *,
    store: Any,
    label: str,
    context: Dict[str, Any],
) -> str:
    """
    label: MID | EOD
    Writes ceo_sessions.md, touches goals_state.json and master_thesis.md.
    """
    working = context.get("what_worked") or "N/A"
    failed = context.get("what_failed") or "N/A"
    edge = context.get("edge_after_fees") or "N/A"
    leak = context.get("leak") or "N/A"
    actions = context.get("actions") or "maintain discipline"

    body = f"WORKING: {working}\nFAILING: {failed}\nEDGE: {edge}\nLEAK: {leak}\nACTIONS: {actions}"
    store.append_md("ceo_sessions.md", f"CEO {label}", body)

    g = store.load_json("goals_state.json")
    g["last_ceo"] = {"label": label, "at": datetime.now(timezone.utc).isoformat()}
    store.save_json("goals_state.json", g)

    try:
        p = store.path("master_thesis.md")
        with p.open("a", encoding="utf-8") as f:
            f.write(f"\n## CEO {label}\n\n{body}\n")
    except OSError as exc:
        logger.warning("master_thesis CEO append: %s", exc)

    return body
