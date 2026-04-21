"""Global trade frequency and capital turnover limits; loss-streak cooldown."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from trading_ai.governance.storage_architecture import shark_state_path

logger = logging.getLogger(__name__)

_STATE_NAME = "reality_lock_capital_velocity.json"


def _path() -> Path:
    return shark_state_path(_STATE_NAME)


def _defaults() -> Dict[str, Any]:
    return {
        "hour_start_ts": 0.0,
        "trades_this_hour": 0,
        "capital_used_this_hour_usd": 0.0,
        "consecutive_losses": 0,
        "cooldown_until_ts": 0.0,
    }


def _load() -> Dict[str, Any]:
    p = _path()
    if not p.is_file():
        return _defaults()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        out = _defaults()
        if isinstance(raw, dict):
            out.update(raw)
        return out
    except Exception as exc:
        logger.warning("capital_velocity load failed: %s", exc)
        return _defaults()


def _save(st: Dict[str, Any]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(st, indent=2), encoding="utf-8")


def _roll_hour(st: Dict[str, Any]) -> None:
    now = time.time()
    start = float(st.get("hour_start_ts") or 0.0)
    if start <= 0 or now - start >= 3600.0:
        st["hour_start_ts"] = now
        st["trades_this_hour"] = 0
        st["capital_used_this_hour_usd"] = 0.0


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.environ.get(name) or "").strip() or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float((os.environ.get(name) or "").strip() or default)
    except ValueError:
        return default


def capital_velocity_allows_trade(
    *,
    venue: str,
    proposed_notional_usd: float,
    account_equity_usd: float,
) -> Tuple[bool, str]:
    """
    Enforce ``max_trades_per_hour``, ``max_capital_turnover_per_hour`` (× account),
    and cooldown after 3 consecutive losses (5 minutes).
    """
    v = (venue or "default").strip().lower()
    _ = v  # per-venue counters could split state later; single ledger for now with venue tag in log

    max_tr = _env_int("REALITY_LOCK_MAX_TRADES_PER_HOUR", 60)
    turnover_mult = _env_float("REALITY_LOCK_MAX_CAPITAL_TURNOVER_PER_HOUR_MULT", 3.0)
    loss_n = _env_int("REALITY_LOCK_CONSECUTIVE_LOSS_COOLDOWN_COUNT", 3)
    cool_sec = _env_float("REALITY_LOCK_LOSS_COOLDOWN_SEC", 300.0)

    st = _load()
    _roll_hour(st)

    now = time.time()
    if now < float(st.get("cooldown_until_ts") or 0.0):
        return False, "cooldown_after_losses"

    if int(st.get("trades_this_hour") or 0) >= max_tr:
        return False, "max_trades_per_hour"

    cap_lim = max(0.0, float(account_equity_usd)) * turnover_mult
    used = float(st.get("capital_used_this_hour_usd") or 0.0)
    if used + float(proposed_notional_usd) > cap_lim and cap_lim > 0:
        return False, "max_capital_turnover_per_hour"

    return True, "ok"


def record_trade_executed(*, notional_usd: float, net_pnl_usd: Optional[float] = None) -> None:
    """Call after a confirmed fill (entry or round-trip close)."""
    st = _load()
    _roll_hour(st)
    st["trades_this_hour"] = int(st.get("trades_this_hour") or 0) + 1
    st["capital_used_this_hour_usd"] = float(st.get("capital_used_this_hour_usd") or 0.0) + float(
        notional_usd
    )
    if net_pnl_usd is not None:
        if float(net_pnl_usd) < -1e-9:
            st["consecutive_losses"] = int(st.get("consecutive_losses") or 0) + 1
        else:
            st["consecutive_losses"] = 0
        if int(st.get("consecutive_losses") or 0) >= _env_int(
            "REALITY_LOCK_CONSECUTIVE_LOSS_COOLDOWN_COUNT", 3
        ):
            st["cooldown_until_ts"] = time.time() + _env_float(
                "REALITY_LOCK_LOSS_COOLDOWN_SEC", 300.0
            )
            st["consecutive_losses"] = 0
    _save(st)
