"""Per-avenue performance from federated trade rows only — no synthetic PnL."""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.global_layer.avenue_truth_contract import normalize_avenue_key
from trading_ai.intelligence.execution_intelligence.time_utils import parse_trade_ts


def net_pnl_for_trade(t: Dict[str, Any]) -> Optional[float]:
    for k in ("net_pnl_usd", "net_pnl"):
        v = t.get(k)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _verdict(
    *,
    n: int,
    pnl_week: float,
    win_rate: Optional[float],
    dd: float,
    vol_label: str,
    consistency: Optional[float],
) -> str:
    """strong | viable | weak | unstable — conservative when data is thin."""
    if n < 3:
        return "weak"
    if n < 5 and (win_rate is None):
        return "weak"
    if vol_label == "high" and pnl_week < 0:
        return "unstable"
    if dd > max(150.0, abs(pnl_week) * 3) and pnl_week <= 0:
        return "unstable"
    if n >= 8 and pnl_week > 0 and (win_rate or 0) >= 0.52 and (consistency or 0) >= 0.45:
        return "strong"
    if pnl_week >= 0 and (win_rate or 0) >= 0.42:
        return "viable"
    if pnl_week < -50 or (win_rate is not None and win_rate < 0.38):
        return "unstable"
    return "weak"


def _pnl_volatility_label(pnls: List[float]) -> str:
    if len(pnls) < 3:
        return "unknown"
    try:
        sd = statistics.pstdev(pnls)
    except statistics.StatisticsError:
        return "unknown"
    if sd < 15:
        return "low"
    if sd < 80:
        return "medium"
    return "high"


def compute_avenue_performance(
    trades: List[Dict[str, Any]],
    *,
    now_ts: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Returns avenues keyed by normalized avenue id, plus summary and data_sufficiency notes.

    All numeric rollups use only rows present in ``trades``; missing net fields excluded from win_rate.
    """
    import time

    now = now_ts if now_ts is not None else time.time()
    day_start = datetime.fromtimestamp(now, tz=timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    t_day = day_start.timestamp()
    week_start = day_start - timedelta(days=day_start.weekday())
    t_week = week_start.timestamp()

    by_av: Dict[str, List[Dict[str, Any]]] = {}
    for t in trades:
        if not isinstance(t, dict):
            continue
        raw = t.get("avenue") or t.get("avenue_id") or t.get("avenue_name")
        av = normalize_avenue_key(raw)
        by_av.setdefault(av, []).append(t)

    avenues_out: Dict[str, Any] = {}
    for av, rows in by_av.items():
        pnls_all: List[float] = []
        pnls_day: List[float] = []
        pnls_week: List[float] = []
        ordered: List[Tuple[float, float]] = []

        for t in rows:
            p = net_pnl_for_trade(t)
            if p is None:
                continue
            pnls_all.append(p)
            ts = parse_trade_ts(t)
            if ts is not None:
                ordered.append((ts, p))
                if ts >= t_day:
                    pnls_day.append(p)
                if ts >= t_week:
                    pnls_week.append(p)

        ordered.sort(key=lambda x: x[0])
        seq = [p for _, p in ordered]

        def _cum_dd(series: List[float]) -> float:
            if not series:
                return 0.0
            peak = 0.0
            cum = 0.0
            worst = 0.0
            for x in series:
                cum += x
                peak = max(peak, cum)
                worst = max(worst, peak - cum)
            return float(worst)

        dd = _cum_dd(seq) if seq else _cum_dd(pnls_all)

        known = [p for p in pnls_all if p is not None]
        wins = sum(1 for p in known if p > 0)
        win_rate = (wins / len(known)) if known else None
        loss_rate = (sum(1 for p in known if p < 0) / len(known)) if known else None
        avg_profit = (sum(known) / len(known)) if known else None

        vol_exp = _pnl_volatility_label(known[-30:] if len(known) > 30 else known)

        # Consistency: compare win_rate last 5 vs prior 5 when possible
        consistency_score: Optional[float] = None
        recent_wr: Optional[float] = None
        if len(known) >= 10:
            a, b = known[-10:-5], known[-5:]
            ra = sum(1 for x in a if x > 0) / 5.0
            rb = sum(1 for x in b if x > 0) / 5.0
            recent_wr = rb
            consistency_score = max(0.0, min(1.0, 1.0 - abs(ra - rb)))
        elif len(known) >= 5:
            consistency_score = max(0.0, min(1.0, (win_rate or 0.0)))

        # Edge stability: inverse of relative std of weekly chunks if we have enough weeks of data
        edge_stability_score: Optional[float] = None
        if len(seq) >= 8:
            chunks = [sum(seq[i : i + 5]) for i in range(0, len(seq) - 4, 3)]
            if len(chunks) >= 2:
                try:
                    sd = statistics.pstdev(chunks)
                    mu = abs(statistics.mean(chunks)) + 1e-6
                    edge_stability_score = max(0.0, min(1.0, 1.0 - min(1.0, sd / mu)))
                except statistics.StatisticsError:
                    edge_stability_score = None

        pnl_day = sum(pnls_day)
        pnl_week = sum(pnls_week)
        pnl_total = sum(pnls_all)

        verdict = _verdict(
            n=len(rows),
            pnl_week=pnl_week,
            win_rate=win_rate,
            dd=dd,
            vol_label=vol_exp,
            consistency=consistency_score,
        )

        avenues_out[av] = {
            "avenue_name": av,
            "pnl_day": round(pnl_day, 4),
            "pnl_week": round(pnl_week, 4),
            "pnl_total": round(pnl_total, 4),
            "trade_count": len(rows),
            "trades_with_known_net": len(known),
            "win_rate": None if win_rate is None else round(win_rate, 4),
            "avg_profit_per_trade": None if avg_profit is None else round(avg_profit, 4),
            "loss_rate": None if loss_rate is None else round(loss_rate, 4),
            "drawdown": round(dd, 4),
            "volatility_exposure": vol_exp,
            "consistency_score": None if consistency_score is None else round(consistency_score, 4),
            "edge_stability_score": None if edge_stability_score is None else round(edge_stability_score, 4),
            "verdict": verdict,
            "recent_win_rate_window": None if recent_wr is None else round(recent_wr, 4),
        }

    strongest = ""
    weakest = ""
    if avenues_out:
        by_week = sorted(avenues_out.items(), key=lambda x: x[1].get("pnl_week", 0.0), reverse=True)
        strongest = str(by_week[0][0])
        weakest = str(by_week[-1][0])

    sufficiency_notes: List[str] = []
    if not trades:
        sufficiency_notes.append("no_trades")
    elif sum(1 for t in trades if isinstance(t, dict) and net_pnl_for_trade(t) is None) > len(trades) // 2:
        sufficiency_notes.append("many_trades_missing_net_pnl")
    low_n = [av for av, row in avenues_out.items() if int(row.get("trade_count") or 0) < 5]
    if low_n:
        sufficiency_notes.append(f"thin_sample_avenues:{','.join(low_n[:6])}")

    return {
        "truth_version": "avenue_performance_v1",
        "avenues": avenues_out,
        "strongest_avenue": strongest,
        "weakest_avenue": weakest,
        "data_sufficiency": {
            "label": "insufficient" if not trades else ("thin" if sufficiency_notes else "adequate"),
            "notes": sufficiency_notes,
        },
    }
