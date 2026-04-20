"""Verify local Trade Intelligence databank files exist and reflect successful trade flow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.nte.databank.local_trade_store import (
    global_trade_events_path,
    path_daily_summary,
    path_weekly_summary,
)
from trading_ai.runtime_paths import ezras_runtime_root


def verify_local_databank_artifacts(
    *,
    trade_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Returns booleans for trade_events.jsonl, daily/weekly summaries — non-empty and optional trade_id line.
    """
    checks: Dict[str, Any] = {}
    te = global_trade_events_path()
    dd = path_daily_summary()
    ww = path_weekly_summary()

    def _ok(p: Path, min_bytes: int = 3) -> bool:
        try:
            return p.is_file() and p.stat().st_size >= min_bytes
        except OSError:
            return False

    checks["trade_events_path"] = str(te)
    checks["trade_events_ok"] = _ok(te, min_bytes=2)
    checks["daily_summary_ok"] = _ok(dd, min_bytes=2)
    checks["weekly_summary_ok"] = _ok(ww, min_bytes=2)

    tid_present = False
    if trade_id and checks["trade_events_ok"]:
        try:
            tail = te.read_text(encoding="utf-8")[-120_000:]
            tid_present = str(trade_id) in tail
        except OSError:
            tid_present = False
    checks["trade_id_in_trade_events"] = tid_present if trade_id else None

    checks["all_core_ok"] = bool(
        checks["trade_events_ok"] and checks["daily_summary_ok"] and checks["weekly_summary_ok"]
    )
    return checks


def write_databank_artifact_proof(*, trade_id: Optional[str] = None) -> Path:
    p = ezras_runtime_root() / "data" / "deployment" / "databank_artifact_proof.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"verified": verify_local_databank_artifacts(trade_id=trade_id)}
    p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return p
