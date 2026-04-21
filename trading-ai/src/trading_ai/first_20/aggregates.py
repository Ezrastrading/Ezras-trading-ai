"""Rollups from diagnostic rows — single source of numeric truth."""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional, Tuple


def _f(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def _i(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except (TypeError, ValueError):
        return default


def compute_running_drawdown(net_pnls: List[float]) -> float:
    """Max drawdown (positive number = dollars below peak equity curve)."""
    peak = 0.0
    cum = 0.0
    max_dd = 0.0
    for p in net_pnls:
        cum += p
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    return max_dd


def aggregate_rows(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(rows)
    wins = 0
    losses = 0
    gross = 0.0
    net = 0.0
    net_series: List[float] = []
    strat: Counter = Counter()
    gate: Counter = Counter()
    avenue: Counter = Counter()
    max_cons_loss = 0
    cur_cons_loss = 0
    dup_b = 0
    gov_b = 0
    adapt_b = 0
    venue_r = 0
    partial = 0
    log_fail = 0
    rebuy_bf = 0

    for r in rows:
        res = str(r.get("result") or "").lower()
        if res == "win":
            wins += 1
            cur_cons_loss = 0
        elif res == "loss":
            losses += 1
            cur_cons_loss += 1
            if cur_cons_loss > max_cons_loss:
                max_cons_loss = cur_cons_loss
        gp = _f(r.get("gross_pnl"))
        npn = _f(r.get("net_pnl"))
        gross += gp
        net += npn
        net_series.append(npn)
        sid = str(r.get("strategy_id") or "unknown")
        gid = str(r.get("gate_id") or "unknown")
        aid = str(r.get("avenue_id") or "unknown")
        strat[sid] += 1
        gate[gid] += 1
        avenue[aid] += 1
        fc = r.get("failure_codes") or []
        if isinstance(fc, list):
            fcs = [str(x).upper() for x in fc]
            if any("DUPLICATE" in x for x in fcs):
                dup_b += 1
            if any("GOVERNANCE" in x for x in fcs):
                gov_b += 1
            if any("ADAPTIVE" in x or "BRAKE" in x for x in fcs):
                adapt_b += 1
            if any("VENUE" in x or "REJECT" in x for x in fcs):
                venue_r += 1
            if any("LOG" in x for x in fcs):
                log_fail += 1
            if any("PARTIAL" in x for x in fcs):
                partial += 1
            if any("REBUY" in x for x in fcs):
                rebuy_bf += 1
        br = str(r.get("blocking_reason") or "").lower()
        if "duplicate" in br:
            dup_b += 1
        if "governance" in br:
            gov_b += 1
        if "adaptive" in br or "brake" in br:
            adapt_b += 1
        if "venue" in br or "reject" in br:
            venue_r += 1
        if "log" in br and "rebuy" not in br:
            log_fail += 1

    win_rate = (wins / n) if n else 0.0
    avg = (net / n) if n else 0.0
    max_dd = compute_running_drawdown(net_series)

    return {
        "trades_completed": n,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "gross_pnl": gross,
        "net_pnl": net,
        "avg_pnl_per_trade": avg,
        "expectancy_per_trade": avg,
        "max_consecutive_losses": max_cons_loss,
        "max_drawdown_seen": max_dd,
        "duplicate_blocks_seen": dup_b,
        "governance_blocks_seen": gov_b,
        "adaptive_brakes_seen": adapt_b,
        "venue_rejects_seen": venue_r,
        "partial_failure_count": partial,
        "logging_failures_seen": log_fail,
        "rebuy_block_failures_seen": rebuy_bf,
        "strategy_mix": dict(strat),
        "gate_mix": dict(gate),
        "avenue_mix": dict(avenue),
        "net_pnls": net_series,
    }


def rolling_last_k(values: List[float], k: int) -> List[float]:
    if not values:
        return []
    return values[-k:]


def consecutive_failure_counts(rows: List[Dict[str, Any]], tag: str) -> int:
    """Count consecutive rows from the end matching integrity or logging failure."""
    tag_u = tag.upper()
    n = 0
    for r in reversed(rows):
        fc = r.get("failure_codes") or []
        hit = False
        if isinstance(fc, list):
            hit = any(tag_u in str(x).upper() for x in fc)
        if not hit:
            break
        n += 1
    return n


def max_failure_repeat(rows: List[Dict[str, Any]]) -> Tuple[int, Optional[str]]:
    """Largest count of identical failure signature across rows."""
    sigs = []
    for r in rows:
        fc = r.get("failure_codes") or []
        if isinstance(fc, list) and fc:
            sigs.append(",".join(sorted(str(x) for x in fc)))
        else:
            br = str(r.get("blocking_reason") or "").strip()
            if br:
                sigs.append(br)
    if not sigs:
        return 0, None
    c = Counter(sigs)
    sig, count = c.most_common(1)[0]
    return int(count), sig
