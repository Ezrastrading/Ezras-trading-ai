"""
First-20 operating protocol documents and readiness helper (does not start trading).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Tuple

from trading_ai.deployment.paths import first_20_protocol_json_path, first_20_protocol_txt_path
from trading_ai.deployment.deployment_models import iso_now


DEFAULT_PROTOCOL: Dict[str, Any] = {
    "schema_version": "1.0",
    "title": "First 20 live trades — operating protocol",
    "size_policy": "smallest_configured_notional_only",
    "max_live_trades": 20,
    "no_scaling_during_first_20": True,
    "checkpoint_reviews_at_trades": [5, 10, 20],
    "stop_immediately_on": [
        "oversell_risk",
        "reconciliation_mismatch",
        "failed_supabase_sync_beyond_retry_flush_policy",
        "governance_inconsistency",
        "execution_anomaly",
        "negative_readiness_flag",
        "system_guard_halt",
    ],
    "notes": "Do not enable first-20 until live micro-validation streak passes and final_readiness approves.",
}


def _txt_from_protocol(d: Dict[str, Any]) -> str:
    lines = [
        "FIRST 20 — OPERATING PROTOCOL",
        "=============================",
        "",
        f"Updated: {d.get('generated_at', '')}",
        "",
        "SIZE: smallest configured notional only. No scaling during first 20.",
        f"Hard cap: {d.get('max_live_trades', 20)} trades.",
        "",
        "CHECKPOINT REVIEWS: trades "
        + ", ".join(str(x) for x in (d.get("checkpoint_reviews_at_trades") or [5, 10, 20])),
        "",
        "STOP IMMEDIATELY ON:",
    ]
    for x in d.get("stop_immediately_on") or []:
        lines.append(f"  - {x}")
    lines.append("")
    lines.append(str(d.get("notes") or ""))
    return "\n".join(lines)


def ensure_first_20_protocol_files() -> Tuple[Path, Path]:
    """Write default JSON + TXT under ``data/deployment`` if missing or empty."""
    jp = first_20_protocol_json_path()
    tp = first_20_protocol_txt_path()
    jp.parent.mkdir(parents=True, exist_ok=True)
    data = dict(DEFAULT_PROTOCOL)
    data["generated_at"] = iso_now()
    if not jp.is_file() or jp.stat().st_size < 10:
        jp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    if not tp.is_file() or tp.stat().st_size < 10:
        tp.write_text(_txt_from_protocol(data), encoding="utf-8")
    return jp, tp


def evaluate_first_20_protocol_readiness() -> Dict[str, Any]:
    """
    Verify protocol artifacts exist and policy fields satisfy deployment gates.

    Does **not** enable first-20 trading.
    """
    ensure_first_20_protocol_files()
    jp = first_20_protocol_json_path()
    ok = True
    reasons: list[str] = []
    payload: Dict[str, Any] = {}
    try:
        raw = json.loads(jp.read_text(encoding="utf-8"))
        payload = raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError):
        ok = False
        reasons.append("protocol_json_unreadable")
        payload = {}

    max_t = int(payload.get("max_live_trades") or 0)
    if max_t != 20:
        ok = False
        reasons.append("max_live_trades_must_be_20")

    if not payload.get("no_scaling_during_first_20", True):
        ok = False
        reasons.append("scaling_must_be_disabled_in_protocol")

    cps = payload.get("checkpoint_reviews_at_trades") or []
    if set(int(x) for x in cps) != {5, 10, 20}:
        ok = False
        reasons.append("checkpoints_must_be_5_10_20")

    stops = payload.get("stop_immediately_on") or []
    blob = " ".join(str(s).lower() for s in stops)
    required = ("oversell", "reconciliation", "supabase", "governance", "halt")
    for r in required:
        if r not in blob:
            ok = False
            reasons.append(f"stop_condition_missing_keyword:{r}")

    return {
        "first_20_protocol_ready": ok,
        "reasons": reasons,
        "protocol_path": str(jp),
        "protocol": payload,
    }
