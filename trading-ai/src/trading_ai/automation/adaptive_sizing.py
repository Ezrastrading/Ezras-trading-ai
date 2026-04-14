"""
Adaptive sizing ladder — single source for sizing multipliers (extends without bucket rewrite).

State: ``{EZRAS_RUNTIME_ROOT}/state/adaptive_sizing_state.json``
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from trading_ai.automation.risk_bucket import runtime_root

_lock = threading.Lock()
_STATE_VERSION = 1

# v1 matches legacy: NORMAL 1.0, REDUCED 0.5, BLOCKED 0.0
DEFAULT_LADDER: Dict[str, float] = {
    "NORMAL": 1.0,
    "REDUCED": 0.5,
    "BLOCKED": 0.0,
    "REDUCED_LIGHT": 0.75,
    "REDUCED_HEAVY": 0.25,
    "RECOVERY": 0.6,
}


def adaptive_sizing_state_path() -> Path:
    return runtime_root() / "state" / "adaptive_sizing_state.json"


def _default_state() -> Dict[str, Any]:
    return {
        "version": _STATE_VERSION,
        "ladder": dict(DEFAULT_LADDER),
        "updated_at": None,
    }


def _load() -> Dict[str, Any]:
    p = adaptive_sizing_state_path()
    if not p.is_file():
        return _default_state()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return _default_state()
        out = _default_state()
        out.update(raw)
        lad = dict(DEFAULT_LADDER)
        lad.update(out.get("ladder") or {})
        out["ladder"] = lad
        return out
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return _default_state()


def _save(data: Dict[str, Any]) -> None:
    p = adaptive_sizing_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(data)
    payload["version"] = _STATE_VERSION
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(p)


def get_ladder_state() -> Dict[str, Any]:
    with _lock:
        return _load()


def get_effective_sizing_multiplier(effective_bucket: str) -> float:
    b = str(effective_bucket or "").strip().upper()
    st = _load()
    ladder: Dict[str, Any] = dict(st.get("ladder") or {})
    for k, v in DEFAULT_LADDER.items():
        ladder.setdefault(k, v)
    return float(ladder.get(b, ladder.get("REDUCED", 0.5)))


def explain_multiplier_decision(effective_bucket: str) -> Dict[str, Any]:
    b = str(effective_bucket or "").strip().upper()
    mult = get_effective_sizing_multiplier(b)
    st = _load()
    return {
        "effective_bucket": b,
        "multiplier": mult,
        "ladder_source": "adaptive_sizing_state.json",
        "ladder_row": (st.get("ladder") or {}).get(b),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
