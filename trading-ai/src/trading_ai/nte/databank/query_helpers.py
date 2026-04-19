"""Query helpers over local trade_events.jsonl — fast filters for monitoring."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from trading_ai.nte.databank.local_trade_store import global_trade_events_path, load_all_trade_events


def all_trades(path: Optional[Any] = None) -> List[Dict[str, Any]]:
    return load_all_trade_events(path)


def first_n_trades(n: int = 20, *, path: Optional[Any] = None) -> List[Dict[str, Any]]:
    ev = all_trades(path)
    return ev[:n]


def trades_by_avenue(avenue_id: str, *, path: Optional[Any] = None) -> List[Dict[str, Any]]:
    aid = avenue_id.strip().upper()
    return [e for e in all_trades(path) if str(e.get("avenue_id") or "").upper() == aid]


def trades_by_strategy(strategy_id: str, *, path: Optional[Any] = None) -> List[Dict[str, Any]]:
    return [e for e in all_trades(path) if str(e.get("strategy_id")) == strategy_id]


def losing_with_positive_expected_edge(*, path: Optional[Any] = None) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for e in all_trades(path):
        try:
            exp = float(e.get("expected_edge_bps") or 0)
            net = float(e.get("net_pnl") or 0)
        except (TypeError, ValueError):
            continue
        if exp > 0 and net < 0:
            out.append(e)
    return out


def high_slippage(min_total_slippage_bps: float = 50.0, *, path: Optional[Any] = None) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for e in all_trades(path):
        try:
            s = abs(float(e.get("entry_slippage_bps") or 0)) + abs(float(e.get("exit_slippage_bps") or 0))
        except (TypeError, ValueError):
            continue
        if s >= min_total_slippage_bps:
            out.append(e)
    return out


def degraded_mode_trades(*, path: Optional[Any] = None) -> List[Dict[str, Any]]:
    return [e for e in all_trades(path) if e.get("degraded_mode")]


def stale_cancelled_entries(*, path: Optional[Any] = None) -> List[Dict[str, Any]]:
    return [e for e in all_trades(path) if e.get("stale_cancelled")]


def route_a_vs_route_b_stats(*, path: Optional[Any] = None) -> Dict[str, Any]:
    """Aggregate when route scores exist; flag when chosen route disagrees with scores."""
    a_better = 0
    b_better = 0
    tie = 0
    chosen_a = 0
    chosen_b = 0
    chosen_a_when_b_higher = 0
    for e in all_trades(path):
        ch = str(e.get("route_chosen") or "").upper()
        if ch == "A":
            chosen_a += 1
        elif ch == "B":
            chosen_b += 1
        ra = e.get("route_a_score")
        rb = e.get("route_b_score")
        if ra is None or rb is None:
            continue
        try:
            ra_f, rb_f = float(ra), float(rb)
        except (TypeError, ValueError):
            continue
        if ra_f > rb_f:
            a_better += 1
        elif rb_f > ra_f:
            b_better += 1
            if ch == "A":
                chosen_a_when_b_higher += 1
        else:
            tie += 1
    return {
        "pairs_with_scores": a_better + b_better + tie,
        "route_a_score_higher": a_better,
        "route_b_score_higher": b_better,
        "tie_scores": tie,
        "chosen_route_A_count": chosen_a,
        "chosen_route_B_count": chosen_b,
        "chosen_A_when_B_score_higher": chosen_a_when_b_higher,
    }


def avenue_comparison(*, path: Optional[Any] = None) -> Dict[str, Dict[str, Any]]:
    """Net PnL and trade count by avenue A/B/C."""
    acc: Dict[str, Dict[str, Any]] = {}
    for e in all_trades(path):
        aid = str(e.get("avenue_id") or "?")
        acc.setdefault(aid, {"avenue_id": aid, "trade_count": 0, "net_pnl": 0.0, "gross_pnl": 0.0, "fees": 0.0})
        acc[aid]["trade_count"] += 1
        try:
            acc[aid]["net_pnl"] += float(e.get("net_pnl") or 0)
            acc[aid]["gross_pnl"] += float(e.get("gross_pnl") or 0)
            acc[aid]["fees"] += float(e.get("fees_paid") or 0)
        except (TypeError, ValueError):
            pass
    return acc


def maker_vs_taker_performance(*, path: Optional[Any] = None) -> Dict[str, Dict[str, float]]:
    out = {"maker": {"trades": 0, "net_pnl": 0.0}, "taker": {"trades": 0, "net_pnl": 0.0}, "unknown": {"trades": 0, "net_pnl": 0.0}}
    for e in all_trades(path):
        m = str(e.get("maker_taker") or "unknown").lower()
        bucket = m if m in out else "unknown"
        out[bucket]["trades"] += 1
        try:
            out[bucket]["net_pnl"] += float(e.get("net_pnl") or 0)
        except (TypeError, ValueError):
            pass
    return out


def fees_by_strategy(*, path: Optional[Any] = None) -> Dict[str, float]:
    fees: Dict[str, float] = {}
    for e in all_trades(path):
        sid = str(e.get("strategy_id") or "unknown")
        try:
            fees[sid] = fees.get(sid, 0.0) + float(e.get("fees_paid") or 0)
        except (TypeError, ValueError):
            pass
    return fees


def fees_by_avenue(*, path: Optional[Any] = None) -> Dict[str, float]:
    fees: Dict[str, float] = {}
    for e in all_trades(path):
        aid = str(e.get("avenue_id") or "?")
        try:
            fees[aid] = fees.get(aid, 0.0) + float(e.get("fees_paid") or 0)
        except (TypeError, ValueError):
            pass
    return fees


def regime_best_worst(*, path: Optional[Any] = None) -> Dict[str, Any]:
    from collections import defaultdict

    pnl_by: Dict[str, float] = defaultdict(float)
    for e in all_trades(path):
        r = str(e.get("regime") or "unknown")
        try:
            pnl_by[r] += float(e.get("net_pnl") or 0)
        except (TypeError, ValueError):
            pass
    if not pnl_by:
        return {"best_regime": None, "worst_regime": None, "by_regime": {}}
    best = max(pnl_by, key=lambda k: pnl_by[k])
    worst = min(pnl_by, key=lambda k: pnl_by[k])
    return {"best_regime": best, "worst_regime": worst, "by_regime": dict(pnl_by)}
