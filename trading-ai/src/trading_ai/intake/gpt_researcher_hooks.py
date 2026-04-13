from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from trading_ai.config import Settings
from trading_ai.models.schemas import SourceRef

logger = logging.getLogger(__name__)

# One resolution per pipeline run (reset via reset_gpt_researcher_runtime_state).
_runtime: dict[str, object] = {
    "checked": False,
    "will_run": False,
    "base_argv": None,  # Optional[List[str]] — executable + fixed args; query appended per call
}


def reset_gpt_researcher_runtime_state() -> None:
    """Call at the start of each pipeline run so the command is re-resolved once per run."""
    _runtime["checked"] = False
    _runtime["will_run"] = False
    _runtime["base_argv"] = None


def _resolve_gpt_researcher_base_argv(settings: Settings) -> Optional[List[str]]:
    """
    Resolve GPT_RESEARCHER_COMMAND to a list [executable, ...optional_args].
    Accepts absolute/relative paths to scripts, or a name found on PATH.

    If the value is a single path whose parent directories contain spaces, it must
    not be passed through shlex.split first (that would break the path).
    """
    raw = (settings.gpt_researcher_command or "").strip()
    if not raw:
        return None

    cleaned = raw.strip().strip('"').strip("'")
    p = Path(cleaned).expanduser()
    try:
        p = p.resolve()
    except OSError:
        p = None
    else:
        if p.is_file() and os.access(p, os.X_OK):
            return [str(p)]

    try:
        parts = shlex.split(raw, posix=os.name != "nt")
    except ValueError:
        return None
    if not parts:
        return None

    head = parts[0]
    if head.startswith("~") or os.sep in head or (len(head) > 1 and head.startswith("." + os.sep)):
        p2 = Path(head).expanduser()
        try:
            p2 = p2.resolve()
        except OSError:
            return None
        if p2.is_file() and os.access(p2, os.X_OK):
            return [str(p2)] + parts[1:]
        return None

    found = shutil.which(head)
    if found and os.access(found, os.X_OK):
        return [found] + parts[1:]
    return None


def run_gpt_researcher_hook(
    settings: Settings,
    query: str,
    timeout_sec: int = 900,
) -> Tuple[Optional[str], List[SourceRef]]:
    """
    Optional hook: run GPT_RESEARCHER_COMMAND (any executable or wrapper script) with the
    market query as the final argument. GPT_RESEARCHER_ENABLED is the master switch.
    """
    if not settings.gpt_researcher_enabled:
        return None, []

    if not _runtime["checked"]:
        _runtime["checked"] = True
        raw_cmd = (settings.gpt_researcher_command or "").strip()
        if not raw_cmd:
            _runtime["will_run"] = False
            logger.warning(
                "GPT_RESEARCHER_ENABLED=true but GPT_RESEARCHER_COMMAND is empty; "
                "set it to an executable or wrapper script path (see README). Skipping GPT Researcher this run."
            )
        else:
            base = _resolve_gpt_researcher_base_argv(settings)
            if base is None:
                _runtime["will_run"] = False
                logger.warning(
                    "GPT_RESEARCHER_ENABLED=true but GPT_RESEARCHER_COMMAND does not resolve to an "
                    "executable file: %r — check the path and chmod +x. Skipping GPT Researcher this run.",
                    raw_cmd,
                )
            else:
                _runtime["will_run"] = True
                _runtime["base_argv"] = base

    if not _runtime["will_run"]:
        return None, []

    base_argv = _runtime["base_argv"]
    assert isinstance(base_argv, list) and base_argv
    cmd = [*base_argv, query]
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
