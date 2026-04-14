"""
Rolling temporal baselines (1d / 7d / 30d) for doctrine/consistency verdict trends.

Deterministic classification: stable | degrading | oscillating | chronic_drift.
"""

from __future__ import annotations

import json
import logging
import statistics
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.automation.risk_bucket import runtime_root

logger = logging.getLogger(__name__)
_lock = threading.Lock()

STATE_FILE = "temporal_consistency_state.json"
LOG_FILE = "temporal_consistency_log.md"

VERDICT_SCORE = {
    "ALIGNED": 0.0,
    "PARTIALLY_ALIGNED": 1.0,
    "DRIFTING": 2.0,
    "DOCTRINE_VIOLATION": 3.0,
    "HALT": 4.0,
}


def _state_path() -> Path:
    return runtime_root() / "state" / STATE_FILE


def _log_path() -> Path:
    return runtime_root() / "logs" / LOG_FILE


def _load_state() -> Dict[str, Any]:
    p = _state_path()
    if not p.is_file():
        return {"version": 1, "samples": []}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {"version": 1, "samples": []}
        raw.setdefault("samples", [])
        return raw
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "samples": []}


def _save_state(data: Dict[str, Any]) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp.replace(p)


def _append_log(line: str) -> None:
    try:
        lp = _log_path()
        lp.parent.mkdir(parents=True, exist_ok=True)
        with open(lp, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as exc:
        logger.warning("temporal_consistency log: %s", exc)


def record_temporal_event(event_kind: str, source: str = "activation") -> None:
    """Record a non-doctrine lifecycle marker (activation, registry, heartbeat correlation)."""
    record_verdict_sample("ALIGNED", rule_triggered=f"event:{event_kind}", source=source)


def record_verdict_sample(verdict: str, *, rule_triggered: str = "", source: str = "engine") -> None:
    """Append a scored sample (called from consistency engine on each evaluation)."""
    ts = datetime.now(timezone.utc).isoformat()
    score = VERDICT_SCORE.get(verdict, 2.0)
    row = {
        "timestamp": ts,
        "verdict": verdict,
        "score": score,
        "rule": rule_triggered,
        "source": source,
    }
    with _lock:
        st = _load_state()
        st["samples"].append(row)
        # cap memory: keep last 4000 samples (~ months at high freq)
        st["samples"] = st["samples"][-4000:]
        _save_state(st)
    _append_log(f"- {ts} | {verdict} | {rule_triggered} | {source}")


def _window_samples(hours: float) -> List[Dict[str, Any]]:
    st = _load_state()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out: List[Dict[str, Any]] = []
    for s in st["samples"]:
        try:
            t = datetime.fromisoformat(str(s["timestamp"]).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if t >= cutoff:
            out.append(s)
    return out


def _trend_class(scores: List[float]) -> str:
    if len(scores) < 3:
        return "insufficient_data"
    n = len(scores)
    first_third = scores[: max(1, n // 3)]
    last_third = scores[-max(1, n // 3) :]
    early = statistics.mean(first_third)
    late = statistics.mean(last_third)
    if late > early + 0.75:
        return "degrading"
    if early > late + 0.75:
        return "improving"
    # oscillation: high stdev
    if statistics.pstdev(scores) > 1.25:
        return "oscillating"
    if late >= 2.5 and early >= 2.0:
        return "chronic_drift"
    if late <= 1.0 and statistics.pstdev(scores) < 0.5:
        return "stable"
    return "watch"


def build_temporal_summary() -> Dict[str, Any]:
    """Structured summary for ``consistency temporal`` CLI."""
    windows: Tuple[Tuple[str, float], ...] = (
        ("1d", 24.0),
        ("7d", 24 * 7.0),
        ("30d", 24 * 30.0),
    )
    out: Dict[str, Any] = {"windows": {}, "overall_trend": "unknown"}
    all_recent_scores: List[float] = []

    for label, hrs in windows:
        samp = _window_samples(hrs)
        scores = [float(s.get("score", 0)) for s in samp]
        all_recent_scores = scores
        drift_ct = sum(1 for s in samp if s.get("verdict") in ("DRIFTING", "DOCTRINE_VIOLATION", "HALT"))
        tclass = _trend_class(scores) if scores else "insufficient_data"
        out["windows"][label] = {
            "sample_count": len(samp),
            "mean_score": round(statistics.mean(scores), 4) if scores else None,
            "drift_or_violation_events": drift_ct,
            "classification": tclass,
        }

    # overall from 30d if available else 7d
    s30 = _window_samples(24 * 30)
    scores30 = [float(s.get("score", 0)) for s in s30]
    if len(scores30) >= 3:
        out["overall_trend"] = _trend_class(scores30)
    elif all_recent_scores:
        out["overall_trend"] = _trend_class(all_recent_scores)
    else:
        out["overall_trend"] = "insufficient_data"

    out["state_path"] = str(_state_path())
    out["log_path"] = str(_log_path())
    return out
