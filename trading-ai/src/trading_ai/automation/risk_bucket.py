"""
Account risk bucket (NORMAL / REDUCED / BLOCKED) from recent closes + equity drawdown.

State: ``{EZRAS_RUNTIME_ROOT}/state/risk_state.json`` (default: ``~/ezras-runtime/state/``).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)
_lock = threading.Lock()

_STATE_VERSION = 1

_DEFAULT_RUNTIME = Path.home() / "ezras-runtime"


def runtime_root() -> Path:
    raw = (os.environ.get("EZRAS_RUNTIME_ROOT") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return _DEFAULT_RUNTIME.resolve()


def risk_state_path() -> Path:
    return runtime_root() / "state" / "risk_state.json"


def _default_state() -> Dict[str, Any]:
    return {
        "version": _STATE_VERSION,
        "equity_index": 100.0,
        "peak_equity_index": 100.0,
        "recent_results": [],  # "win" | "loss", oldest first, max 10
        "processed_close_ids": [],  # de-dupe replays; keep last 64 ids
    }


def _load_state() -> Dict[str, Any]:
    p = risk_state_path()
    if not p.is_file():
        return _default_state()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return _default_state()
        out = _default_state()
        out.update(raw)
        out.setdefault("recent_results", [])
        out.setdefault("processed_close_ids", [])
        out["equity_index"] = float(out.get("equity_index") or 100.0)
        out["peak_equity_index"] = float(out.get("peak_equity_index") or 100.0)
        if not isinstance(out["recent_results"], list):
            out["recent_results"] = []
        if not isinstance(out["processed_close_ids"], list):
            out["processed_close_ids"] = []
        return out
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return _default_state()


def _save_state(data: Dict[str, Any]) -> None:
    p = risk_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(data)
    payload["version"] = _STATE_VERSION
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(p)


def _drawdown_pct(equity: float, peak: float) -> float:
    if peak <= 0:
        return 0.0
    return max(0.0, (peak - equity) / peak * 100.0)


def get_account_risk_bucket(trade_event: Optional[Dict[str, Any]] = None) -> str:
    """
    Returns ``NORMAL``, ``REDUCED``, or ``BLOCKED`` from persisted state.

    ``trade_event`` is reserved for future use (e.g. phase=open|closed); v1 ignores it for logic.
    """
    _ = trade_event  # API stability
    try:
        st = _load_state()
        recent: List[str] = [str(x).lower() for x in (st.get("recent_results") or []) if x]
        recent = [x for x in recent if x in ("win", "loss")]
        equity = float(st.get("equity_index") or 100.0)
        peak = float(st.get("peak_equity_index") or 100.0)
        dd = _drawdown_pct(equity, peak)

        last3 = recent[-3:]
        last5 = recent[-5:]
        losses3 = sum(1 for x in last3 if x == "loss")
        losses5 = sum(1 for x in last5 if x == "loss")

        if losses5 >= 4 or dd > 10.0:
            return "BLOCKED"
        if losses3 >= 2 or dd > 5.0:
            return "REDUCED"
        return "NORMAL"
    except Exception as exc:
        logger.warning("get_account_risk_bucket fallback NORMAL: %s", exc)
        return "NORMAL"


def record_closed_trade(trade: Dict[str, Any]) -> None:
    """
    Append outcome to recent window and update equity index from ``roi_percent``.
    Idempotent per ``trade_id`` (skips if already processed).
    Never raises.
    """
    try:
        tid = str(trade.get("trade_id") or "").strip()
        if not tid:
            return
        res = str(trade.get("result") or "").strip().lower()
        if res not in ("win", "loss"):
            return

        with _lock:
            st = _load_state()
            seen: List[str] = list(st.get("processed_close_ids") or [])
            if tid in seen[-64:]:
                return

            roi = float(trade.get("roi_percent") or 0.0)
            eq = float(st.get("equity_index") or 100.0)
            eq *= 1.0 + roi / 100.0
            peak = max(float(st.get("peak_equity_index") or 100.0), eq)

            rr: List[str] = list(st.get("recent_results") or [])
            rr.append("win" if res == "win" else "loss")
            rr = rr[-10:]

            seen.append(tid)
            seen = seen[-64:]

            st.update(
                {
                    "equity_index": round(eq, 6),
                    "peak_equity_index": round(peak, 6),
                    "recent_results": rr,
                    "processed_close_ids": seen,
                }
            )
            _save_state(st)
    except Exception as exc:
        logger.warning("record_closed_trade skipped: %s", exc)
