"""Softmax capital allocation across venues with floors/caps and drawdown emergency cut."""

from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from trading_ai.runtime_paths import ezras_runtime_root

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    try:
        return float((os.environ.get(name) or "").strip() or default)
    except ValueError:
        return default


REBALANCE_INTERVAL_SEC_MIN = _env_float("CAPITAL_ROUTER_REBALANCE_SEC_MIN", 1800.0)
REBALANCE_INTERVAL_SEC_MAX = _env_float("CAPITAL_ROUTER_REBALANCE_SEC_MAX", 3600.0)
MAX_VENUE_DRAWDOWN = _env_float("MAX_VENUE_DRAWDOWN", 0.25)

PNL_WEIGHT = _env_float("VENUE_SCORE_PNL_WEIGHT", 1.0)
WINRATE_WEIGHT = _env_float("VENUE_SCORE_WINRATE_WEIGHT", 0.5)
DRAWDOWN_PENALTY = _env_float("VENUE_SCORE_DRAWDOWN_PENALTY", 1.5)
EXECUTION_WEIGHT = _env_float("VENUE_SCORE_EXECUTION_WEIGHT", 0.4)

MIN_VENUE_FRAC = _env_float("CAPITAL_ROUTER_MIN_VENUE_FRAC", 0.10)
MAX_VENUE_FRAC = _env_float("CAPITAL_ROUTER_MAX_VENUE_FRAC", 0.60)
SOFTMAX_TEMPERATURE = _env_float("CAPITAL_ROUTER_SOFTMAX_TEMPERATURE", 1.0)


def venue_scores_path() -> Path:
    return ezras_runtime_root() / "venue_scores.json"


@dataclass
class VenuePerformance:
    venue: str
    last_50_trades_pnl: float = 0.0
    win_rate: float = 0.0
    drawdown: float = 0.0
    execution_quality_score: float = 0.5
    shutdown_flag: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)


def venue_raw_score(vp: VenuePerformance) -> float:
    """Higher is better before softmax."""
    npnl = max(
        -1.0,
        min(
            1.0,
            math.tanh(vp.last_50_trades_pnl / max(1.0, abs(vp.last_50_trades_pnl) + 100.0)),
        ),
    )
    wr = max(0.0, min(1.0, vp.win_rate))
    dd = max(0.0, min(1.0, vp.drawdown))
    ex = max(0.0, min(1.0, vp.execution_quality_score))
    return (
        PNL_WEIGHT * npnl
        + WINRATE_WEIGHT * wr
        - DRAWDOWN_PENALTY * dd
        + EXECUTION_WEIGHT * ex
    )


def allocation_softmax(
    performances: Sequence[VenuePerformance],
    *,
    min_frac: float = MIN_VENUE_FRAC,
    max_frac: float = MAX_VENUE_FRAC,
    temperature: float = SOFTMAX_TEMPERATURE,
) -> Dict[str, float]:
    """
    Map venue scores through softmax, then clamp [min_frac, max_frac] and renormalize.
    Venues with ``shutdown_flag`` or emergency drawdown get weight 0 before floor.
    """
    if not performances:
        return {}
    temp = max(1e-6, float(temperature))
    scores: List[float] = []
    keys: List[str] = []
    shutdown: List[bool] = []
    for vp in performances:
        v = str(vp.venue).strip().lower()
        keys.append(v)
        if vp.shutdown_flag or vp.drawdown > MAX_VENUE_DRAWDOWN:
            shutdown.append(True)
            scores.append(-1e9)
        else:
            shutdown.append(False)
            scores.append(venue_raw_score(vp))

    mx = max(scores) if scores else 0.0
    exps = [math.exp((s - mx) / temp) if not shutdown[i] else 0.0 for i, s in enumerate(scores)]
    ssum = sum(exps)
    if ssum <= 1e-12:
        n = max(len(keys), 1)
        raw = {keys[i]: 1.0 / n for i in range(len(keys))}
    else:
        raw = {keys[i]: exps[i] / ssum for i in range(len(keys))}

    # clamp and renormalize
    lo = max(0.0, min(1.0, min_frac))
    hi = max(lo, min(1.0, max_frac))
    clamped = {k: max(lo, min(hi, raw[k])) for k in raw}
    tot = sum(clamped.values())
    if tot <= 1e-12:
        n = max(len(clamped), 1)
        return {k: 1.0 / n for k in clamped}
    return {k: v / tot for k, v in clamped.items()}


def load_venue_performances_from_disk(path: Optional[Path] = None) -> Dict[str, VenuePerformance]:
    p = path or venue_scores_path()
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, VenuePerformance] = {}
    for k, v in raw.items():
        if not isinstance(v, dict):
            continue
        vid = str(k).strip().lower()
        out[vid] = VenuePerformance(
            venue=vid,
            last_50_trades_pnl=float(v.get("last_50_trades_pnl") or 0.0),
            win_rate=float(v.get("win_rate") or 0.0),
            drawdown=float(v.get("drawdown") or 0.0),
            execution_quality_score=float(v.get("execution_quality_score") or 0.5),
            shutdown_flag=bool(v.get("shutdown_flag")),
            extra=dict(v.get("extra") or {}),
        )
    return out


def save_venue_scores_snapshot(
    performances: Mapping[str, VenuePerformance],
    allocation: Mapping[str, float],
    *,
    path: Optional[Path] = None,
) -> None:
    p = path or venue_scores_path()
    payload: Dict[str, Any] = {
        "updated_unix": time.time(),
        "allocation": dict(allocation),
        "venues": {
            k: {
                "last_50_trades_pnl": v.last_50_trades_pnl,
                "win_rate": v.win_rate,
                "drawdown": v.drawdown,
                "execution_quality_score": v.execution_quality_score,
                "shutdown_flag": v.shutdown_flag,
            }
            for k, v in performances.items()
        },
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def apply_router_to_portfolio_engine(
    engine: Any,
    performances: Sequence[VenuePerformance],
    *,
    persist: bool = True,
) -> Dict[str, Any]:
    """
    Set ``PortfolioEngine`` state fractions from softmax allocation.

    Does **not** bypass :meth:`PortfolioEngine.rebalance` profit-reality checks — call from
    the same contexts where rebalance is allowed, or use dry-run by passing a cloned engine.
    """
    alloc = allocation_softmax(performances)
    by_venue = {str(p.venue).strip().lower(): p for p in performances}
    # merge with engine avenues
    avenues = getattr(engine, "_avenues", None)
    if avenues:
        for a in avenues:
            a = str(a).strip().lower()
            if a not in alloc and a not in by_venue:
                alloc[a] = 1.0 / max(len(avenues), 1)
    st = engine.state
    st.capital_fraction_by_avenue = {k: float(v) for k, v in alloc.items()}
    engine._normalize_fractions(st)
    try:
        engine._save(st)
    except Exception as exc:
        logger.debug("portfolio save after router: %s", exc)
    if persist:
        save_venue_scores_snapshot(by_venue, dict(st.capital_fraction_by_avenue))
    return {"ok": True, "allocation": dict(st.capital_fraction_by_avenue)}


def router_rebalance_due(last_unix: float, *, now: Optional[float] = None) -> bool:
    """True if last rebalance was outside [min,max] window (uses max as interval)."""
    t = float(now or time.time())
    return (t - float(last_unix or 0.0)) >= REBALANCE_INTERVAL_SEC_MAX
