"""
Controlled rollout for the first N live trades — reduced size, checkpoints, halt on failure.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.governance.storage_architecture import shark_state_path

logger = logging.getLogger(__name__)

FIRST_N = 20
SCALE_FIRST_N = 0.25
CHECKPOINTS = (5, 10, 20)
_STATE_NAME = "first_20_protocol.json"


def _path() -> Path:
    return shark_state_path(_STATE_NAME)


def _default_state() -> Dict[str, Any]:
    return {
        "total_trades": 0,
        "halted": False,
        "halt_reason": "",
        "checkpoints_logged": [],
        "detailed_logging": True,
    }


def _load() -> Dict[str, Any]:
    p = _path()
    if not p.is_file():
        st = _default_state()
        _save(st)
        return st
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            base = _default_state()
            base.update(raw)
            return base
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("first_20 state load failed: %s", exc)
    return _default_state()


def _save(data: Dict[str, Any]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_state() -> Dict[str, Any]:
    return dict(_load())


def is_halted() -> bool:
    return bool(_load().get("halted"))


def mode() -> str:
    st = _load()
    if int(st.get("total_trades") or 0) < FIRST_N:
        return "FIRST_20"
    return "NORMAL"


def apply_position_scale(base_notional: float) -> Tuple[float, str]:
    """During FIRST_20, scale notional by SCALE_FIRST_N."""
    st = _load()
    if st.get("halted"):
        return 0.0, "halted"
    n = int(st.get("total_trades") or 0)
    if n >= FIRST_N:
        try:
            from trading_ai.intelligence.deployment_decision import full_scale_notional_permitted

            if not full_scale_notional_permitted():
                return float(base_notional) * SCALE_FIRST_N, "PROFIT_REALITY_HOLD"
        except Exception:
            pass
        return float(base_notional), "NORMAL"
    return float(base_notional) * SCALE_FIRST_N, "FIRST_20"


def on_execution_failure(reason: str) -> None:
    """Halt immediately if still in validation phase."""
    st = _load()
    if int(st.get("total_trades") or 0) >= FIRST_N:
        return
    st["halted"] = True
    st["halt_reason"] = str(reason)[:2000]
    _save(st)
    logger.critical("first_20_protocol: HALT during validation: %s", reason)
    if (os.environ.get("FIRST_20_SYSTEM_WIDE_HALT") or "").strip().lower() in ("1", "true", "yes"):
        try:
            from trading_ai.core.system_guard import get_system_guard

            get_system_guard().halt_now(f"first_20_halt:{reason}")
        except Exception:
            logger.debug("first_20 halt: system_guard unavailable", exc_info=True)


def on_execution_success() -> None:
    st = _load()
    if st.get("halted"):
        return
    n = int(st.get("total_trades") or 0) + 1
    st["total_trades"] = n
    logged: List[Any] = list(st.get("checkpoints_logged") or [])
    for cp in CHECKPOINTS:
        if n == cp and cp not in logged:
            logged.append(cp)
            logger.warning("first_20_protocol: CHECKPOINT trade_count=%s mode=FIRST_20", cp)
    st["checkpoints_logged"] = logged
    st["detailed_logging"] = n < FIRST_N
    _save(st)
