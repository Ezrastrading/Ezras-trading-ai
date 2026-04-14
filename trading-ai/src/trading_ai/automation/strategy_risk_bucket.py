"""
Strategy-scoped risk buckets (nested containment vs account-wide only).

State: ``{EZRAS_RUNTIME_ROOT}/state/strategy_risk_state.json``
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List

from trading_ai.automation.risk_bucket import runtime_root

logger = logging.getLogger(__name__)
_lock = threading.Lock()

_STATE_VERSION = 1


def strategy_risk_state_path() -> Path:
    return runtime_root() / "state" / "strategy_risk_state.json"


def _default_state() -> Dict[str, Any]:
    return {"version": _STATE_VERSION, "strategies": {}}


def _load() -> Dict[str, Any]:
    p = strategy_risk_state_path()
    if not p.is_file():
        return _default_state()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return _default_state()
        out = _default_state()
        out.update(raw)
        out.setdefault("strategies", {})
        return out
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return _default_state()


def _save(data: Dict[str, Any]) -> None:
    p = strategy_risk_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(data)
    payload["version"] = _STATE_VERSION
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(p)


def strategy_key_from_trade(trade: Dict[str, Any]) -> str:
    for k in ("strategy_id", "strategy_key", "market_category"):
        v = trade.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()[:128]
    return "default"


def _bucket_from_recent(recent: List[str]) -> str:
    recent = [str(x).lower() for x in recent if x]
    recent = [x for x in recent if x in ("win", "loss")]
    if not recent:
        return "NORMAL"
    last3 = recent[-3:]
    last5 = recent[-5:]
    losses3 = sum(1 for x in last3 if x == "loss")
    losses5 = sum(1 for x in last5 if x == "loss")
    if losses5 >= 4:
        return "BLOCKED"
    if losses3 >= 2:
        return "REDUCED"
    return "NORMAL"


def get_strategy_risk_bucket(trade_or_key: Any) -> str:
    """Return NORMAL|REDUCED|BLOCKED for strategy."""
    if isinstance(trade_or_key, str):
        key = trade_or_key
    else:
        key = strategy_key_from_trade(trade_or_key or {})
    try:
        st = _load()
        ent = (st.get("strategies") or {}).get(key) or {}
        recent: List[str] = list(ent.get("recent_results") or [])
        return _bucket_from_recent(recent)
    except Exception as exc:
        logger.warning("get_strategy_risk_bucket failsafe REDUCED: %s", exc)
        return "REDUCED"


def record_strategy_closed_trade(trade: Dict[str, Any]) -> None:
    """Append close outcome to strategy window (idempotent per trade_id)."""
    try:
        tid = str(trade.get("trade_id") or "").strip()
        if not tid:
            return
        res = str(trade.get("result") or "").strip().lower()
        if res not in ("win", "loss"):
            return
        key = strategy_key_from_trade(trade)
        with _lock:
            st = _load()
            strat = st.setdefault("strategies", {})
            ent = dict(strat.get(key) or {})
            seen: List[str] = list(ent.get("processed_close_ids") or [])
            if tid in seen[-64:]:
                return
            rr: List[str] = list(ent.get("recent_results") or [])
            rr.append("win" if res == "win" else "loss")
            rr = rr[-10:]
            seen.append(tid)
            seen = seen[-64:]
            ent["recent_results"] = rr
            ent["processed_close_ids"] = seen
            ent["current_strategy_bucket"] = _bucket_from_recent(rr)
            strat[key] = ent
            _save(st)
    except Exception as exc:
        logger.warning("record_strategy_closed_trade skipped: %s", exc)


def worst_of_buckets(account: str, strategy: str) -> str:
    rank = {"BLOCKED": 3, "REDUCED": 2, "NORMAL": 1}
    a = rank.get(str(account).upper(), 1)
    s = rank.get(str(strategy).upper(), 1)
    m = max(a, s)
    inv = {3: "BLOCKED", 2: "REDUCED", 1: "NORMAL"}
    return inv[m]


def resolve_effective_risk_for_open(trade: Dict[str, Any]) -> Dict[str, Any]:
    """Account + strategy layers; effective = worst of account tier and strategy tier."""
    from trading_ai.automation.risk_bucket import get_account_risk_bucket

    ev = {"phase": "open", "trade": trade}
    try:
        b = get_account_risk_bucket(ev)
        raw_a = str(b).strip().upper() if b is not None else "UNKNOWN"
    except Exception:
        raw_a = "UNKNOWN"
    fb = False
    if raw_a in ("NORMAL", "REDUCED", "BLOCKED"):
        acc_eff = raw_a
    else:
        acc_eff = "REDUCED"
        fb = True
    strat = str(get_strategy_risk_bucket(trade)).upper()
    eff = worst_of_buckets(acc_eff, strat)
    return {
        "raw_bucket": raw_a,
        "account_bucket": acc_eff,
        "strategy_bucket": strat,
        "effective_bucket": eff,
        "bucket_fallback_applied": fb,
    }
