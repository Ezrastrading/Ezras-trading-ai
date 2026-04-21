"""
Hard-stop risk engine (artifact-enforced).

Implements:
1. Daily loss cap
2. Rolling drawdown cap
3. Consecutive loss breaker
4. Timeout-loss breaker
5. Fee-dominance breaker

All checks emit `data/risk/risk_state.json` and block trading when status=BLOCKED.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from trading_ai.runtime.trade_ledger import iter_ledger_lines, today_utc
from trading_ai.storage.storage_adapter import LocalStorageAdapter


@dataclass(frozen=True)
class RiskConfig:
    daily_loss_cap_usd: float = 25.0
    rolling_drawdown_cap_usd: float = 60.0
    rolling_window_trades: int = 30
    consecutive_loss_cap: int = 5
    timeout_loss_minutes: float = 30.0
    fee_dominance_ratio: float = 2.0  # fees > ratio * |gross_pnl| counts as fee-dominant


def _safe_float(x: Any) -> float:
    try:
        if x is None or x == "":
            return 0.0
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sum_daily_pnl(lines: Iterable[Dict[str, Any]], day: str) -> float:
    total = 0.0
    for ln in lines:
        ts = ln.get("timestamp_close") or ln.get("timestamp_open")
        try:
            if ts is None:
                continue
            if isinstance(ts, (int, float)):
                d = datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d")
            else:
                s = str(ts).replace("Z", "+00:00")
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                d = dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
            if d != day:
                continue
        except Exception:
            continue
        total += _safe_float(ln.get("pnl"))
    return float(total)


def _rolling_drawdown(lines: List[Dict[str, Any]], n: int) -> float:
    recent = lines[-max(1, int(n)) :]
    pnl = [_safe_float(r.get("pnl")) for r in recent]
    # Drawdown measured as cumulative negative sum (loss magnitude).
    dd = -sum(x for x in pnl if x < 0)
    return float(dd)


def _consecutive_losses(lines: List[Dict[str, Any]]) -> int:
    n = 0
    for ln in reversed(lines):
        p = _safe_float(ln.get("pnl"))
        if p < 0:
            n += 1
        else:
            break
    return int(n)


def _fee_dominance(lines: List[Dict[str, Any]], n: int) -> Tuple[int, List[Dict[str, Any]]]:
    """
    Uses optional keys in ledger lines when present:
      - gross_pnl
      - fees_paid / fees
    If absent, returns 0.
    """
    recent = lines[-max(1, int(n)) :]
    hits = 0
    examples: List[Dict[str, Any]] = []
    for ln in recent:
        gross = _safe_float(ln.get("gross_pnl") or ln.get("gross_pnl_dollars"))
        fees = _safe_float(ln.get("fees_paid") or ln.get("fees") or ln.get("execution_cost_dollars"))
        if fees <= 0:
            continue
        if abs(gross) <= 1e-12:
            continue
        # ratio is fees / |gross|
        ratio = fees / max(1e-12, abs(gross))
        if ratio > 0:
            if ratio >= 2.0:
                hits += 1
                if len(examples) < 5:
                    examples.append(
                        {
                            "trade_id": ln.get("trade_id"),
                            "gross_pnl": gross,
                            "fees": fees,
                            "fee_to_gross_ratio": ratio,
                        }
                    )
    return int(hits), examples


def evaluate_risk_state(
    *,
    runtime_root: Optional[Path] = None,
    config: Optional[RiskConfig] = None,
) -> Dict[str, Any]:
    cfg = config or RiskConfig()
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    root = ad.root()

    lines = list(iter_ledger_lines(runtime_root=root, max_lines=50_000))
    day = today_utc()
    daily_pnl = _sum_daily_pnl(lines, day)
    dd = _rolling_drawdown(lines, cfg.rolling_window_trades)
    cons = _consecutive_losses(lines)
    fee_hits, fee_examples = _fee_dominance(lines, cfg.rolling_window_trades)

    blockers: List[str] = []
    if daily_pnl <= -abs(float(cfg.daily_loss_cap_usd)) - 1e-12:
        blockers.append("daily_loss_cap")
    if dd >= abs(float(cfg.rolling_drawdown_cap_usd)) + 1e-12:
        blockers.append("rolling_drawdown_cap")
    if cons >= int(cfg.consecutive_loss_cap):
        blockers.append("consecutive_loss_breaker")
    if fee_hits >= 3:
        blockers.append("fee_dominance_breaker")

    # Timeout-loss breaker: if last loss occurred recently, enforce cooldown.
    now = time.time()
    last_loss_age_min: Optional[float] = None
    for ln in reversed(lines):
        if _safe_float(ln.get("pnl")) < 0:
            ts = ln.get("timestamp_close") or ln.get("timestamp_open")
            try:
                tsv = float(ts) if isinstance(ts, (int, float, str)) else None
                if tsv is not None:
                    last_loss_age_min = (now - float(tsv)) / 60.0
            except Exception:
                last_loss_age_min = None
            break
    if last_loss_age_min is not None and last_loss_age_min <= float(cfg.timeout_loss_minutes):
        blockers.append("timeout_loss_breaker")

    status = "BLOCKED" if blockers else "ACTIVE"
    state: Dict[str, Any] = {
        "timestamp": _utc_now_iso(),
        "daily_loss": float(daily_pnl),
        "drawdown": float(dd),
        "consecutive_losses": int(cons),
        "status": status,
        "blockers": blockers,
        "fee_dominance": {"hits": fee_hits, "examples": fee_examples},
        "config": {
            "daily_loss_cap_usd": float(cfg.daily_loss_cap_usd),
            "rolling_drawdown_cap_usd": float(cfg.rolling_drawdown_cap_usd),
            "rolling_window_trades": int(cfg.rolling_window_trades),
            "consecutive_loss_cap": int(cfg.consecutive_loss_cap),
            "timeout_loss_minutes": float(cfg.timeout_loss_minutes),
            "fee_dominance_ratio": float(cfg.fee_dominance_ratio),
        },
    }

    ad.write_json("data/risk/risk_state.json", state)
    return state


def risk_allows_or_no_trade(
    *,
    runtime_root: Optional[Path] = None,
    config: Optional[RiskConfig] = None,
) -> Dict[str, Any]:
    rs = evaluate_risk_state(runtime_root=runtime_root, config=config)
    if str(rs.get("status") or "").upper() != "ACTIVE":
        return {
            "action": "NO_TRADE",
            "reason": "risk_blocked:" + ",".join(rs.get("blockers") or ["unknown"]),
            "blocking_layer": "risk",
            "risk_state_path": "data/risk/risk_state.json",
        }
    return {"action": "ALLOW_TRADE", "risk_state_path": "data/risk/risk_state.json"}

