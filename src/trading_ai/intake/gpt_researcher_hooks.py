from __future__ import annotations

import logging
import shutil
import subprocess
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from trading_ai.config import Settings
from trading_ai.models.schemas import SourceRef

logger = logging.getLogger(__name__)


def run_gpt_researcher_hook(
    settings: Settings,
    query: str,
    timeout_sec: int = 120,
) -> Tuple[Optional[str], List[SourceRef]]:
    """
    Optional hook: run external `gpt-researcher` if enabled and on PATH.
    Phase 1 does not bundle GPT Researcher; this is an integration seam.
    """
    if not settings.gpt_researcher_enabled:
        return None, []
    exe = shutil.which(settings.gpt_researcher_command.split()[0])
    if not exe:
        logger.warning("GPT Researcher enabled but command not found: %s", settings.gpt_researcher_command)
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
        logger.warning("GPT Researcher hook timed out")
        return None, []
    out = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    if proc.returncode != 0:
        logger.warning("GPT Researcher hook exit %s: %s", proc.returncode, out[:500])
        return None, []
    now = datetime.now(timezone.utc)
    # Without structured output, treat as a single text note; sources empty in Phase 1.
    return out, [
        SourceRef(
            url=f"gpt-researcher://local/{abs(hash(query))}",
            title="gpt-researcher",
            fetched_at=now,
            provider="gpt_researcher",
        )
    ]
