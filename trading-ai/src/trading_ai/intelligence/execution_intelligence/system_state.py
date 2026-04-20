"""Aggregate operational state from NTE JSON memory + ledger + CEO session log (no synthetic PnL)."""

from __future__ import annotations

import logging
import math
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from trading_ai.nte.capital_ledger import load_ledger, net_equity_estimate
from trading_ai.nte.memory.store import MemoryStore
from trading_ai.nte.paths import nte_memory_dir

from trading_ai.intelligence.execution_intelligence.time_utils import iso_week_id, parse_trade_ts

logger = logging.getLogger(__name__)


def _gate_label(setup_type: Optional[str]) -> str:
    s = (setup_type or "").lower()
    if "mean_reversion" in s:
        return "A"
    if "continuation" in s or "pullback" in s:
        return "B"
    return ""


def _avenue_key(t: Dict[str, Any]) -> str:
    for k in ("avenue", "avenue_id", "avenue_name"):
        v = t.get(k)
        if v is not None and str(v).strip():
            return str(v).strip().lower()
    return ""


def _net_pnl(t: Dict[str, Any]) -> float:
    for k in ("net_pnl_usd", "net_pnl"):
        v = t.get(k)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return 0.0


def _read_ceo_session_digest() -> Dict[str, Any]:
    """Tail of ceo_sessions.md — metadata only, no LLM."""
    p = nte_memory_dir() / "ceo_sessions.md"
    out: Dict[str, Any] = {
        "path": str(p),
        "sections_found": 0,
        "last_section_title": None,
        "tail_excerpt": None,
    }
    if not p.is_file():
        return out
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.debug("ceo_sessions read: %s", exc)
        return out
    lines = text.splitlines()
    headers = [ln for ln in lines if ln.strip().startswith("## ")]
    out["sections_found"] = len(headers)
    if headers:
        out["last_section_title"] = headers[-1].strip("# ").strip()[:200]
    tail = "\n".join(lines[-40:])
    out["tail_excerpt"] = tail[-1200:] if tail else None
    return out


def _volatility_from_market_memory(mm: Dict[str, Any]) -> Dict[str, Any]:
    closes = mm.get("closes") or {}
    if not isinstance(closes, dict):
        return {"label": "unknown", "detail": "invalid closes"}
    series = closes.get("BTC-USD") or closes.get("btc-usd")
    if not isinstance(series, list) or len(series) < 3:
        return {"label": "unknown", "detail": "insufficient BTC closes"}
    vals: List[float] = []
    for x in series[-48:]:
        try:
            vals.append(float(x))
        except (TypeError, ValueError):
            continue
    if len(vals) < 3:
        return {"label": "unknown", "detail": "non-numeric closes"}
    rets: List[float] = []
    for a, b in zip(vals, vals[1:]):
        if a <= 0 or b <= 0:
            continue
        rets.append(math.log(b / a))
    if len(rets) < 2:
        return {"label": "unknown", "detail": "no log returns"}
    sd = float(statistics.pstdev(rets))
    # Rough buckets on per-step log return stdev (not annualized — comparative only)
    if sd < 0.0008:
        label = "low"
    elif sd < 0.0025:
        label = "normal"
    else:
        label = "elevated"
    return {
        "label": label,
        "btc_close_log_return_stdev": round(sd, 6),
        "samples": len(rets),
    }


def _collect_strategy_scores(ss: Dict[str, Any]) -> List[float]:
    out: List[float] = []
    av = ss.get("avenues") or {}
    if not isinstance(av, dict):
        return out
    for _aid, block in av.items():
        if not isinstance(block, dict):
            continue
        for _sk, row in block.items():
            if not isinstance(row, dict):
                continue
            v = row.get("score")
            if v is None:
                continue
            try:
                out.append(float(v))
            except (TypeError, ValueError):
                continue
    return out


def _max_drawdown_cumulative_pnls(pnls: List[float]) -> float:
    if not pnls:
        return 0.0
    peak = 0.0
    cum = 0.0
    worst = 0.0
    for x in pnls:
        cum += x
        peak = max(peak, cum)
        worst = max(worst, peak - cum)
    return float(worst)


def get_system_state(
    *,
    store: Optional[MemoryStore] = None,
    now_ts: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Returns operational metrics derived from ``trade_memory.json``, ``market_memory.json``,
    ``strategy_scores.json``, capital ledger, and ``ceo_sessions.md``.

    Missing inputs surface as ``None`` or empty collections — never invented trade rows.
    """
    st = store or MemoryStore()
    st.ensure_defaults()
    tm = st.load_json("trade_memory.json")
    mm = st.load_json("market_memory.json")
    ss = st.load_json("strategy_scores.json")

    led = load_ledger()
    capital_total = float(net_equity_estimate())

    now = now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()
    day_start = datetime.fromtimestamp(now, tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    t_day = day_start.timestamp()
    week_start = day_start - timedelta(days=day_start.weekday())
    t_week = week_start.timestamp()

    raw_trades: List[Dict[str, Any]] = [t for t in (tm.get("trades") or []) if isinstance(t, dict)]

    daily_pnls: List[float] = []
    weekly_pnls: List[float] = []
    trade_count_today = 0
    all_pnls_ordered: List[Tuple[float, float]] = []  # (ts, pnl)

    for t in raw_trades:
        ts = parse_trade_ts(t)
        pnl = _net_pnl(t)
        if ts is not None:
            all_pnls_ordered.append((ts, pnl))
            if ts >= t_day:
                daily_pnls.append(pnl)
                trade_count_today += 1
            if ts >= t_week:
                weekly_pnls.append(pnl)
        else:
            # Untimestamped rows: include in totals but not in time windows (honest)
            pass

    daily_pnl = sum(daily_pnls)
    weekly_pnl = sum(weekly_pnls)

    trade_count_week = 0
    for t in raw_trades:
        ts = parse_trade_ts(t)
        if ts is not None and ts >= t_week:
            trade_count_week += 1

    pnls_for_stats = [_net_pnl(t) for t in raw_trades]
    wins = [p for p in pnls_for_stats if p > 0]
    losses = [p for p in pnls_for_stats if p < 0]
    breakeven = sum(1 for p in pnls_for_stats if p == 0)
    n = len(pnls_for_stats)
    win_rate = (len(wins) / n) if n else None
    loss_rate = (len(losses) / n) if n else None
    avg_profit_per_trade = (sum(pnls_for_stats) / n) if n else None

    all_pnls_ordered.sort(key=lambda x: x[0])
    ordered_pnls = [p for _, p in all_pnls_ordered]
    max_drawdown = _max_drawdown_cumulative_pnls(ordered_pnls)

    # Active avenues: appeared in last 30d with at least one trade
    cutoff = now - 30 * 86400
    active_avenues: Set[str] = set()
    for t in raw_trades:
        ts = parse_trade_ts(t)
        if ts is None or ts < cutoff:
            continue
        ak = _avenue_key(t)
        if ak:
            active_avenues.add(ak)

    active_gates: Set[str] = set()
    for t in raw_trades:
        g = _gate_label(str(t.get("setup_type") or ""))
        if g:
            active_gates.add(g)

    sc_vals = _collect_strategy_scores(ss)
    if len(sc_vals) >= 2:
        try:
            sd = statistics.pstdev(sc_vals)
            edge_stability_score = max(0.0, min(1.0, 1.0 - min(1.0, sd * 2.0)))
        except statistics.StatisticsError:
            edge_stability_score = None
    elif len(sc_vals) == 1:
        edge_stability_score = None
    else:
        edge_stability_score = None

    recent_trade_outcomes: List[Dict[str, Any]] = []
    tail = list(raw_trades)[-15:]
    for t in tail:
        recent_trade_outcomes.append(
            {
                "trade_id": str(t.get("trade_id") or t.get("id") or "")[:80] or None,
                "net_pnl_usd": _net_pnl(t),
                "avenue": _avenue_key(t) or None,
                "gate": _gate_label(str(t.get("setup_type") or "")) or None,
            }
        )

    vol = _volatility_from_market_memory(mm)

    data_quality = {
        "trade_rows": len(raw_trades),
        "trades_with_parseable_ts": sum(1 for t in raw_trades if parse_trade_ts(t) is not None),
        "unattributed_avenue_rows": sum(1 for t in raw_trades if not _avenue_key(t)),
    }

    return {
        "capital_total": capital_total,
        "daily_pnl": daily_pnl,
        "weekly_pnl": weekly_pnl,
        "trade_count_today": trade_count_today,
        "trade_count_week": trade_count_week,
        "win_rate": win_rate,
        "avg_profit_per_trade": avg_profit_per_trade,
        "loss_rate": loss_rate,
        "breakeven_trade_count": breakeven,
        "max_drawdown": max_drawdown,
        "current_active_avenues": sorted(active_avenues),
        "active_gates": sorted(active_gates),
        "edge_stability_score": edge_stability_score,
        "recent_trade_outcomes": recent_trade_outcomes,
        "volatility_state": vol,
        "ceo_session_log": _read_ceo_session_digest(),
        "ledger_snapshot": {
            "realized_pnl_net": float(led.get("realized_pnl_net") or led.get("realized_pnl_usd") or 0.0),
            "rolling_7d_net_profit": float(led.get("rolling_7d_net_profit") or 0.0),
            "rolling_30d_net_profit": float(led.get("rolling_30d_net_profit") or 0.0),
        },
        "strategy_scores_updated": ss.get("updated"),
        "trade_memory_updated": tm.get("updated"),
        "data_quality": data_quality,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def weekly_net_by_avenue(
    trades: List[Dict[str, Any]],
    *,
    week_start_ts: float,
    now_ts: float,
) -> Dict[str, float]:
    """Sum net PnL per avenue for trades with ts in [week_start_ts, now_ts)."""
    out: Dict[str, float] = {}
    for t in trades:
        ts = parse_trade_ts(t)
        if ts is None or ts < week_start_ts or ts > now_ts:
            continue
        av = _avenue_key(t)
        if not av:
            continue
        out[av] = out.get(av, 0.0) + _net_pnl(t)
    return out


def global_weekly_totals_by_iso_week(
    trades: List[Dict[str, Any]],
    *,
    now_ts: float,
) -> Dict[str, float]:
    """ISO week id -> global net sum."""
    buckets: Dict[str, float] = {}
    for t in trades:
        ts = parse_trade_ts(t)
        if ts is None:
            continue
        wid = iso_week_id(ts)
        buckets[wid] = buckets.get(wid, 0.0) + _net_pnl(t)
    return buckets
