from __future__ import annotations

import logging
import shutil
import subprocess
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from trading_ai.config import Settings
from trading_ai.models.schemas import SourceRef

logger = logging.getLogger(__name__)

# One detection per pipeline run (reset via reset_gpt_researcher_runtime_state).
_runtime: dict[str, object] = {
    "checked": False,
    "will_run": False,
}


def reset_gpt_researcher_runtime_state() -> None:
    """Call at the start of each pipeline run so availability is re-checked once per run."""
    _runtime["checked"] = False
    _runtime["will_run"] = False


def run_gpt_researcher_hook(
    settings: Settings,
    query: str,
    timeout_sec: int = 120,
) -> Tuple[Optional[str], List[SourceRef]]:
    """
    Optional hook: run external GPT Researcher CLI if enabled and available.
    If GPT_RESEARCHER_ENABLED is false, returns quietly with no logs.
    If enabled but the command is not on PATH, logs one warning per run and skips all candidates.
    """
    if not settings.gpt_researcher_enabled:
        return None, []

    if not _runtime["checked"]:
        _runtime["checked"] = True
        cmd0 = settings.gpt_researcher_command.split()[0]
        exe = shutil.which(cmd0)
        if not exe:
            _runtime["will_run"] = False
            logger.warning(
                "GPT_RESEARCHER_ENABLED=true but '%s' is not on PATH; skipping GPT Researcher for this run "
                "(set GPT_RESEARCHER_ENABLED=false to silence, or install the CLI)",
                settings.gpt_researcher_command,
            )
        else:
            _runtime["will_run"] = True

    if not _runtime["will_run"]:
        return None, []

    cmd = [*settings.gpt_researcher_command.split(), query]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("GPT Researcher hook timed out for query (truncated): %s", query[:80])
        return None, []
    out = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    if proc.returncode != 0:
        logger.warning("GPT Researcher hook exit %s: %s", proc.returncode, out[:500])
        return None, []
    now = datetime.now(timezone.utc)
    return out, [
        SourceRef(
            url=f"gpt-researcher://local/{abs(hash(query))}",
            title="gpt-researcher",
            fetched_at=now,
            provider="gpt_researcher",
        )
    ]
