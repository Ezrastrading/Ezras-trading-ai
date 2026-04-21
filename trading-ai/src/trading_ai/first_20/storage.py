"""Filesystem helpers for first-20 artifacts (append-only JSONL, atomic-ish JSON writes)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from trading_ai.first_20.constants import (
    P_ADJUSTMENTS,
    P_DIAGNOSTICS,
    P_EXEC_QUALITY,
    P_EDGE_QUALITY,
    P_FINAL_JSON,
    P_FINAL_TXT,
    P_LESSONS_JSON,
    P_LESSONS_TXT,
    P_OPERATOR_JSON,
    P_OPERATOR_TXT,
    P_OPERATOR_ACK,
    P_PASS_DECISION,
    P_PAUSE_REASON,
    P_REBUY_AUDIT,
    P_SCOREBOARD_JSON,
    P_SCOREBOARD_TXT,
    P_TRUTH,
    default_rebuy_audit,
    default_truth_contract,
)
from trading_ai.storage.storage_adapter import LocalStorageAdapter


def adapter(runtime_root: Optional[Path] = None) -> LocalStorageAdapter:
    return LocalStorageAdapter(runtime_root=runtime_root)


def append_jsonl(relative_path: str, row: Dict[str, Any], *, runtime_root: Optional[Path] = None) -> None:
    ad = adapter(runtime_root)
    line = json.dumps(row, default=str) + "\n"
    ad.ensure_parent(relative_path)
    p = ad.root() / relative_path
    with open(p, "a", encoding="utf-8") as f:
        f.write(line)


def read_jsonl(relative_path: str, *, runtime_root: Optional[Path] = None) -> List[Dict[str, Any]]:
    ad = adapter(runtime_root)
    p = ad.root() / relative_path
    if not p.is_file():
        return []
    out: List[Dict[str, Any]] = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
                if isinstance(o, dict):
                    out.append(o)
            except json.JSONDecodeError:
                continue
    return out


def write_text(relative_path: str, text: str, *, runtime_root: Optional[Path] = None) -> None:
    adapter(runtime_root).write_text(relative_path, text)


def write_json(relative_path: str, payload: Dict[str, Any], *, runtime_root: Optional[Path] = None) -> None:
    adapter(runtime_root).write_json(relative_path, payload)


def read_json(relative_path: str, *, runtime_root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    return adapter(runtime_root).read_json(relative_path)


def ensure_bootstrap(runtime_root: Optional[Path] = None) -> None:
    """Create minimal honest templates if missing (does not overwrite)."""
    ad = adapter(runtime_root)
    if not ad.exists(P_TRUTH):
        write_json(P_TRUTH, default_truth_contract(), runtime_root=runtime_root)
    if not ad.exists(P_REBUY_AUDIT):
        write_json(P_REBUY_AUDIT, default_rebuy_audit(), runtime_root=runtime_root)
    placeholders: Dict[str, Any] = {
        P_SCOREBOARD_JSON: {"status": "empty", "honesty": "Awaiting first diagnostic refresh."},
        P_EXEC_QUALITY: {"score_0_to_100": 0, "pass": False, "exact_weaknesses": [], "exact_recommended_next_step": "Activate diagnostic phase and complete trades."},
        P_EDGE_QUALITY: {"score_0_to_100": 0, "pass": False, "exact_weaknesses": [], "exact_recommended_next_step": "Activate diagnostic phase and complete trades."},
        P_PAUSE_REASON: {"paused": False, "reasons": []},
        P_PASS_DECISION: {"passed": False, "failed": False, "manual_review_required": True},
        P_LESSONS_JSON: {"milestones": {}, "honesty": "No lessons until diagnostic rows exist."},
        P_OPERATOR_JSON: {"safe_to_continue": False, "honesty": "Not evaluated."},
        P_FINAL_JSON: {"FIRST_20_PHASE_ACTIVE": False, "honesty": "Bootstrap placeholder only."},
    }
    for rel, body in placeholders.items():
        if not ad.exists(rel):
            write_json(rel, body, runtime_root=runtime_root)
    for rel in (P_SCOREBOARD_TXT, P_LESSONS_TXT, P_OPERATOR_TXT, P_FINAL_TXT):
        if not ad.exists(rel):
            write_text(rel, "", runtime_root=runtime_root)


def trade_ids_in_diagnostics(*, runtime_root: Optional[Path] = None) -> Iterable[str]:
    for row in read_jsonl(P_DIAGNOSTICS, runtime_root=runtime_root):
        tid = str(row.get("trade_id") or "").strip()
        if tid:
            yield tid


def operator_ack_fresh(*, runtime_root: Optional[Path] = None, max_age_hours: float) -> bool:
    doc = read_json(P_OPERATOR_ACK, runtime_root=runtime_root)
    if not isinstance(doc, dict):
        return False
    ts = str(doc.get("acknowledged_at_iso") or "").strip()
    if not ts:
        return False
    try:
        from datetime import datetime, timezone

        t0 = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if t0.tzinfo is None:
            t0 = t0.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - t0).total_seconds() / 3600.0
        return age <= max_age_hours
    except Exception:
        return False


def max_drawdown_config() -> float:
    raw = (os.environ.get("FIRST_20_MAX_DRAWDOWN_USD") or "").strip()
    if not raw:
        return 500.0
    try:
        return float(raw)
    except ValueError:
        return 500.0


def operator_ack_hours() -> float:
    raw = (os.environ.get("FIRST_20_OPERATOR_ACK_MAX_AGE_HOURS") or "").strip()
    if not raw:
        return 72.0
    try:
        return float(raw)
    except ValueError:
        return 72.0
