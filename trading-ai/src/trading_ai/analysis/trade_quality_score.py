"""
Post-close trade quality — independent of win/loss.

Log: ``{EZRAS_RUNTIME_ROOT}/logs/trade_quality_log.md``
State: ``{EZRAS_RUNTIME_ROOT}/state/trade_quality_state.json``
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.automation.risk_bucket import runtime_root

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_STATE_VERSION = 1


def _state_path() -> Path:
    return runtime_root() / "state" / "trade_quality_state.json"


def _log_path() -> Path:
    return runtime_root() / "logs" / "trade_quality_log.md"


def _default_state() -> Dict[str, Any]:
    return {"version": _STATE_VERSION, "scores": []}


def _load() -> Dict[str, Any]:
    p = _state_path()
    if not p.is_file():
        return _default_state()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return _default_state()
        out = _default_state()
        out.update(raw)
        out.setdefault("scores", [])
        return out
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return _default_state()


def _save(data: Dict[str, Any]) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(data)
    payload["version"] = _STATE_VERSION
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(p)


def _append_log(row: Dict[str, Any]) -> None:
    try:
        _log_path().parent.mkdir(parents=True, exist_ok=True)
        ts = row.get("timestamp") or datetime.now(timezone.utc).isoformat()
        block = f"\n## {ts}\n\n```json\n{json.dumps(row, indent=2, default=str)}\n```\n\n---\n"
        with open(_log_path(), "a", encoding="utf-8") as f:
            f.write(block)
    except Exception as exc:
        logger.warning("trade_quality log append failed: %s", exc)


def score_closed_trade(trade: Dict[str, Any], reconciliation: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Deterministic heuristic scores 0–10. Winning trades can score low; losing trades high.
    """
    tid = str(trade.get("trade_id") or "").strip() or "unknown"
    roi = float(trade.get("roi_percent") or 0.0)
    exit_reason = str(trade.get("exit_reason") or "").lower()
    rec = reconciliation or {}

    setup_quality = 7.0
    if trade.get("signal_score") is not None:
        try:
            setup_quality = min(10.0, max(0.0, float(trade.get("signal_score"))))
        except (TypeError, ValueError):
            pass

    execution_quality = 8.0
    if rec.get("price_slippage") is not None:
        try:
            slip = float(rec["price_slippage"])
            execution_quality = max(0.0, 10.0 - slip * 100.0)
        except (TypeError, ValueError):
            execution_quality = 6.0
    if rec.get("execution_quality_verdict") not in (None, "CLEAN"):
        execution_quality = min(execution_quality, 5.5)

    sizing_quality = 8.0
    meta = trade.get("position_sizing_meta") or {}
    if meta.get("approval_status") == "REDUCED":
        sizing_quality = 7.5
    if meta.get("bucket_fallback_applied"):
        sizing_quality = 6.5

    exit_quality = 7.0
    if "stop" in exit_reason or "plan" in exit_reason:
        exit_quality = 8.5
    if "panic" in exit_reason or "early" in exit_reason:
        exit_quality = 4.0

    rule_adherence = 10.0 if meta else 7.0

    overall = (
        setup_quality * 0.2
        + execution_quality * 0.25
        + sizing_quality * 0.15
        + exit_quality * 0.2
        + rule_adherence * 0.2
    )
    _ = roi  # outcome does not dominate overall_quality_score

    verdict = "MEDIUM"
    if overall >= 7.5:
        verdict = "HIGH"
    elif overall < 5.0:
        verdict = "LOW"

    notes: List[str] = []
    if rec.get("price_slippage") and float(rec["price_slippage"] or 0) > 0.02:
        notes.append("slippage above expected")
    if meta.get("effective_bucket") == "REDUCED":
        notes.append("reduced sizing regime at open")

    out = {
        "trade_id": tid,
        "setup_quality": round(setup_quality, 2),
        "execution_quality": round(execution_quality, 2),
        "sizing_quality": round(sizing_quality, 2),
        "exit_quality": round(exit_quality, 2),
        "rule_adherence": round(rule_adherence, 2),
        "overall_quality_score": round(overall, 2),
        "quality_verdict": verdict,
        "notes": notes,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with _lock:
        st = _load()
        scores: List[Dict[str, Any]] = list(st.get("scores") or [])
        scores.append(out)
        st["scores"] = scores[-512:]
        try:
            _save(st)
        except Exception as exc:
            logger.warning("trade_quality save failed: %s", exc)
    _append_log(out)
    return out
