"""Aggregate operational state from NTE JSON memory + ledger + CEO session log (no synthetic PnL)."""

from __future__ import annotations

import logging
import math
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from trading_ai.intelligence.execution_intelligence.edge_stability import compute_edge_stability_bundle
from trading_ai.intelligence.execution_intelligence.metrics_common import max_drawdown_cumulative_pnls
from trading_ai.intelligence.ts_parse import iso_week_id, parse_trade_ts
from trading_ai.intelligence.truth_contract import policy_for_runtime
from trading_ai.nte.capital_ledger import load_ledger, net_equity_estimate
from trading_ai.nte.memory.store import MemoryStore
from trading_ai.nte.paths import nte_memory_dir

from trading_ai.intelligence.execution_intelligence.ceo_session_store import load_latest_structured_session

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
    """Structured CEO session first, then markdown mirror (metadata only)."""
    structured = load_latest_structured_session()
    p = nte_memory_dir() / "ceo_sessions.md"
    md_meta: Dict[str, Any] = {
        "path": str(p),
        "sections_found": 0,
        "last_section_title": None,
        "tail_excerpt": None,
    }
    if p.is_file():
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            headers = [ln for ln in lines if ln.strip().startswith("## ")]
            md_meta["sections_found"] = len(headers)
            if headers:
                md_meta["last_section_title"] = headers[-1].strip("# ").strip()[:200]
            tail = "\n".join(lines[-40:])
            md_meta["tail_excerpt"] = tail[-1200:] if tail else None
        except OSError as exc:
            logger.debug("ceo_sessions read: %s", exc)
    return {
        "truth_version": "ceo_digest_v2",
        "primary_machine_source": "ceo_session_structured_latest.json",
        "structured_latest": structured,
        "markdown_mirror": md_meta,
    }


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


def get_system_state(
    *,
    store: Optional[MemoryStore] = None,
    now_ts: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Operational metrics from **NTE** ``trade_memory.json`` (runtime truth), plus ledger,
    ``market_memory.json``, ``strategy_scores.json``, and CEO digest.

    ``source_policy_used`` is always :data:`policy_for_runtime` — federated review truth is separate.
    """
    from trading_ai.intelligence.resolved_trades import compare_ledger_to_trade_sum, resolve_for_runtime

    st = store or MemoryStore()
    st.ensure_defaults()
    rt_resolve = resolve_for_runtime(st)
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
    usable = rt_resolve.get("rows_for_windows") or []

    daily_pnls: List[float] = []
    weekly_pnls: List[float] = []
    trade_count_today = 0
    all_pnls_ordered: List[Tuple[float, float]] = []  # (ts, pnl)

    for t in usable:
        ts = parse_trade_ts(t)
        pnl = _net_pnl(t)
        if ts is not None:
            all_pnls_ordered.append((ts, pnl))
            if ts >= t_day:
                daily_pnls.append(pnl)
                trade_count_today += 1
            if ts >= t_week:
                weekly_pnls.append(pnl)

    daily_pnl = sum(daily_pnls)
    weekly_pnl = sum(weekly_pnls)

    trade_count_week = 0
    for t in usable:
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

    # Active avenues: appeared in last 30d with at least one trade
    cutoff = now - 30 * 86400
    active_avenues: Set[str] = set()
    for t in usable:
        ts = parse_trade_ts(t)
        if ts is None or ts < cutoff:
            continue
        ak = _avenue_key(t)
        if ak:
            active_avenues.add(ak)

    active_gates: Set[str] = set()
    for t in usable:
        g = _gate_label(str(t.get("setup_type") or ""))
        if g:
            active_gates.add(g)

    all_pnls_ordered.sort(key=lambda x: x[0])
    ordered_pnls_seq = [p for _, p in all_pnls_ordered]
    max_drawdown = max_drawdown_cumulative_pnls(ordered_pnls_seq)
    edge_bundle = compute_edge_stability_bundle(
        raw_trades=raw_trades,
        strategy_scores_doc=ss,
        ordered_pnls_chronological=ordered_pnls_seq,
    )
    edge_stability_score = edge_bundle.get("edge_stability_score")
    edge_stability_confidence = edge_bundle.get("edge_stability_confidence")
    edge_stability_components = edge_bundle.get("edge_stability_components")
    edge_stability_honesty = edge_bundle.get("honesty_note")

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

    dq_resolve = rt_resolve.get("data_quality") or {}
    data_quality = {
        "trade_rows": len(raw_trades),
        "trades_with_parseable_ts": sum(1 for t in raw_trades if parse_trade_ts(t) is not None),
        "unattributed_avenue_rows": sum(1 for t in raw_trades if not _avenue_key(t)),
        "normalization": dq_resolve,
        "rows_usable_for_windows": len(usable),
    }
    goal_truth_discrepancy = compare_ledger_to_trade_sum(rt_resolve.get("rows_normalized") or [])

    return {
        "source_policy_used": policy_for_runtime(),
        "runtime_trade_resolution": {
            "truth_version": rt_resolve.get("truth_version"),
            "rows_raw_count": rt_resolve.get("rows_raw_count"),
            "federation_meta": rt_resolve.get("federation_meta"),
        },
        "goal_truth_discrepancy": goal_truth_discrepancy,
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
        "edge_stability_confidence": edge_stability_confidence,
        "edge_stability_components": edge_stability_components,
        "edge_stability_honesty": edge_stability_honesty,
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
