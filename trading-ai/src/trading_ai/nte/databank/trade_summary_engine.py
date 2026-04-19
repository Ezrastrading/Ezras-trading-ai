"""Daily / weekly / monthly / strategy / avenue summaries from closed trade events."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from trading_ai.nte.databank.databank_schema import DATABANK_SCHEMA_VERSION
from trading_ai.nte.databank.local_trade_store import (
    path_avenue_performance,
    path_daily_summary,
    path_monthly_summary,
    path_strategy_performance,
    path_weekly_summary,
    save_aggregate,
)


def _parse_close_ts(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _num(x: Any, d: float = 0.0) -> float:
    try:
        return float(x) if x is not None else d
    except (TypeError, ValueError):
        return d


def _win(net_pnl: float) -> bool:
    return net_pnl >= 0.0


def _maker_flag(maker_taker: str) -> Optional[bool]:
    m = (maker_taker or "").lower()
    if m == "maker":
        return True
    if m == "taker":
        return False
    return None


def rebuild_summaries_from_events(events: List[Mapping[str, Any]]) -> Dict[str, Any]:
    """Rebuild all summary JSON blobs from a full event list."""
    daily: Dict[str, Dict[str, Any]] = {}
    weekly: Dict[str, Dict[str, Any]] = {}
    monthly: Dict[str, Dict[str, Any]] = {}
    strategy_rows: Dict[str, Dict[str, Any]] = {}
    avenue_rows: Dict[str, Dict[str, Any]] = {}

    for ev in events:
        ts = _parse_close_ts(ev.get("timestamp_close"))
        if ts is None:
            continue
        ts = ts.astimezone(timezone.utc)
        day = ts.strftime("%Y-%m-%d")
        iso_year, iso_week, _ = ts.isocalendar()
        week_key = f"{iso_year}-W{iso_week:02d}"
        month_key = ts.strftime("%Y-%m")
        aid = str(ev.get("avenue_id") or "?")
        aname = str(ev.get("avenue_name") or "")
        sid = str(ev.get("strategy_id") or "unknown")
        net = _num(ev.get("net_pnl"))
        gross = _num(ev.get("gross_pnl"))
        fees = _num(ev.get("fees_paid"))
        spread = _num(ev.get("spread_bps_entry"))
        slip = abs(_num(ev.get("entry_slippage_bps"))) + abs(_num(ev.get("exit_slippage_bps")))
        hold = _num(ev.get("hold_seconds"))
        mt = str(ev.get("maker_taker") or "unknown")
        stale = bool(ev.get("stale_cancelled"))
        tq = _num(ev.get("trade_quality_score"))

        dkey = f"{day}|{aid}|{sid}"
        _acc_bucket(daily, dkey, net, gross, fees, spread, slip, hold, mt, stale, tq, win=_win(net))

        wkey = f"{week_key}|{aid}|{sid}"
        _acc_bucket(weekly, wkey, net, gross, fees, spread, slip, hold, mt, stale, tq, win=_win(net))

        mkey = f"{month_key}|{aid}|{sid}"
        _acc_bucket(monthly, mkey, net, gross, fees, spread, slip, hold, mt, stale, tq, win=_win(net))

        sk = f"strategy|{aid}|{sid}|all"
        _acc_bucket(strategy_rows, sk, net, gross, fees, spread, slip, hold, mt, stale, tq, win=_win(net), regime=str(ev.get("regime") or ""))

        ak = f"avenue|{aid}|{aname}|all"
        _acc_bucket(avenue_rows, ak, net, gross, fees, spread, slip, hold, mt, stale, tq, win=_win(net), strategy_id=sid)

    now = datetime.now(timezone.utc).isoformat()

    daily_out = _finalize_buckets(daily, "daily", now)
    weekly_out = _finalize_buckets(weekly, "weekly", now)
    monthly_out = _finalize_buckets(monthly, "monthly", now)
    strat_out = _finalize_strategy(strategy_rows, now)
    ave_out = _finalize_avenue(avenue_rows, now)

    return {
        "daily": daily_out,
        "weekly": weekly_out,
        "monthly": monthly_out,
        "strategy": strat_out,
        "avenue": ave_out,
    }


def _acc_bucket(
    store: Dict[str, Dict[str, Any]],
    key: str,
    net: float,
    gross: float,
    fees: float,
    spread: float,
    slip: float,
    hold: float,
    maker_taker: str,
    stale: bool,
    tq: float,
    *,
    win: bool,
    regime: str = "",
    strategy_id: str = "",
) -> None:
    b = store.setdefault(
        key,
        {
            "_nets": [],
            "_gross": [],
            "_fees": [],
            "_spreads": [],
            "_slips": [],
            "_holds": [],
            "_tq": [],
            "_wins": 0,
            "_maker": 0,
            "_taker": 0,
            "_stale": 0,
            "_regimes": defaultdict(int),
            "_strategies": defaultdict(float),
        },
    )
    b["_nets"].append(net)
    b["_gross"].append(gross)
    b["_fees"].append(fees)
    b["_spreads"].append(spread)
    b["_slips"].append(slip)
    b["_holds"].append(hold)
    b["_tq"].append(tq)
    if win:
        b["_wins"] += 1
    mf = _maker_flag(maker_taker)
    if mf is True:
        b["_maker"] += 1
    elif mf is False:
        b["_taker"] += 1
    if stale:
        b["_stale"] += 1
    if regime:
        b["_regimes"][regime] += net
    if strategy_id:
        b["_strategies"][strategy_id] += net


def _finalize_buckets(raw: Dict[str, Dict[str, Any]], period: str, created_at: str) -> Dict[str, Any]:
    rollups: List[Dict[str, Any]] = []
    by_key: Dict[str, Any] = {}
    for key, b in raw.items():
        parts = key.split("|")
        trade_count = len(b["_nets"])
        if trade_count == 0:
            continue
        wins = b["_wins"]
        wr = wins / trade_count if trade_count else 0.0
        nets = b["_nets"]
        pos = [x for x in nets if x > 0]
        neg = [x for x in nets if x < 0]
        avg_win = sum(pos) / len(pos) if pos else 0.0
        avg_loss = sum(neg) / len(neg) if neg else 0.0
        gross = sum(b["_gross"])
        fees = sum(b["_fees"])
        net = sum(nets)
        maker = b["_maker"]
        taker = b["_taker"]
        denom = maker + taker
        maker_pct = (maker / denom * 100.0) if denom else 0.0
        taker_pct = (taker / denom * 100.0) if denom else 0.0
        avg_spread = sum(b["_spreads"]) / trade_count
        avg_slip = sum(b["_slips"]) / trade_count
        avg_hold = sum(b["_holds"]) / trade_count
        stale_rate = b["_stale"] / trade_count * 100.0

        summary_id = f"{period}:{key}"
        row = {
            "summary_id": summary_id,
            "summary_date": parts[0] if period == "daily" else parts[0],
            "avenue_id": parts[1] if len(parts) > 1 else None,
            "strategy_id": parts[2] if len(parts) > 2 else None,
            "trade_count": trade_count,
            "win_rate": round(wr, 6),
            "avg_win": round(avg_win, 6),
            "avg_loss": round(avg_loss, 6),
            "gross_pnl": round(gross, 6),
            "fees_paid": round(fees, 6),
            "net_pnl": round(net, 6),
            "maker_pct": round(maker_pct, 4),
            "taker_pct": round(taker_pct, 4),
            "avg_spread_bps": round(avg_spread, 4),
            "avg_slippage_bps": round(avg_slip, 4),
            "avg_hold_seconds": round(avg_hold, 4),
            "cancel_rate_pct": 0.0,
            "stale_pending_rate_pct": round(stale_rate, 4),
            "created_at": created_at,
            "schema_version": DATABANK_SCHEMA_VERSION,
        }
        rollups.append(row)
        by_key[key] = row
    return {"rollups": rollups, "by_key": by_key, "updated": created_at}


def _finalize_strategy(raw: Dict[str, Dict[str, Any]], created_at: str) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for key, b in raw.items():
        trade_count = len(b["_nets"])
        if trade_count == 0:
            continue
        nets = b["_nets"]
        net = sum(nets)
        gross = sum(b["_gross"])
        fees = sum(b["_fees"])
        wins = b["_wins"]
        wr = wins / trade_count if trade_count else 0.0
        pos = [x for x in nets if x > 0]
        neg = [x for x in nets if x < 0]
        avg_win = sum(pos) / len(pos) if pos else 0.0
        avg_loss = sum(neg) / len(neg) if neg else 0.0
        expectancy = net / trade_count if trade_count else 0.0
        regimes = dict(b["_regimes"])
        best_regime = max(regimes, key=lambda k: regimes[k]) if regimes else None
        worst_regime = min(regimes, key=lambda k: regimes[k]) if regimes else None
        tq_avg = sum(b["_tq"]) / trade_count if trade_count else 0.0
        _, aid, sid, _ = key.split("|", 3)
        rows.append(
            {
                "strategy_summary_id": f"strategy:{aid}:{sid}:all",
                "avenue_id": aid,
                "strategy_id": sid,
                "period_type": "all_time_local",
                "start_ts": None,
                "end_ts": None,
                "trade_count": trade_count,
                "win_rate": round(wr, 6),
                "avg_win": round(avg_win, 6),
                "avg_loss": round(avg_loss, 6),
                "gross_pnl": round(gross, 6),
                "fees_paid": round(fees, 6),
                "net_pnl": round(net, 6),
                "expectancy": round(expectancy, 6),
                "best_regime": best_regime,
                "worst_regime": worst_regime,
                "avg_execution_score": None,
                "avg_edge_score": None,
                "avg_trade_quality_score": round(tq_avg, 4),
                "created_at": created_at,
                "schema_version": DATABANK_SCHEMA_VERSION,
            }
        )
    return {"rows": rows, "updated": created_at}


def _finalize_avenue(raw: Dict[str, Dict[str, Any]], created_at: str) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for key, b in raw.items():
        trade_count = len(b["_nets"])
        if trade_count == 0:
            continue
        nets = b["_nets"]
        net = sum(nets)
        gross = sum(b["_gross"])
        fees = sum(b["_fees"])
        tq_avg = sum(b["_tq"]) / trade_count if trade_count else 0.0
        spreads = sum(b["_spreads"]) / trade_count
        slips = sum(b["_slips"]) / trade_count
        parts = key.split("|")
        aid = parts[1] if len(parts) > 1 else "?"
        aname = parts[2] if len(parts) > 2 else ""
        strat_pnls = dict(b["_strategies"])
        strongest = max(strat_pnls, key=lambda k: strat_pnls[k]) if strat_pnls else None
        weakest = min(strat_pnls, key=lambda k: strat_pnls[k]) if strat_pnls else None
        rows.append(
            {
                "avenue_summary_id": f"avenue:{aid}:all",
                "avenue_id": aid,
                "avenue_name": aname,
                "period_type": "all_time_local",
                "start_ts": None,
                "end_ts": None,
                "trade_count": trade_count,
                "net_pnl": round(net, 6),
                "gross_pnl": round(gross, 6),
                "fees_paid": round(fees, 6),
                "avg_trade_quality_score": round(tq_avg, 4),
                "strongest_strategy_id": strongest,
                "weakest_strategy_id": weakest,
                "avg_spread_bps": round(spreads, 4),
                "avg_slippage_bps": round(slips, 4),
                "created_at": created_at,
                "schema_version": DATABANK_SCHEMA_VERSION,
            }
        )
    return {"rows": rows, "updated": created_at}


def write_summary_files_from_rebuild(rebuilt: Dict[str, Any]) -> None:
    save_aggregate(path_daily_summary(), rebuilt["daily"])
    save_aggregate(path_weekly_summary(), rebuilt["weekly"])
    save_aggregate(path_monthly_summary(), rebuilt["monthly"])
    save_aggregate(path_strategy_performance(), rebuilt["strategy"])
    save_aggregate(path_avenue_performance(), rebuilt["avenue"])


def refresh_all_summaries(events: List[Mapping[str, Any]]) -> None:
    rebuilt = rebuild_summaries_from_events(list(events))
    write_summary_files_from_rebuild(rebuilt)


def update_goal_snapshot_hook(
    events: List[Mapping[str, Any]],
    *,
    active_goal: str = "global",
    current_equity: Optional[float] = None,
    speed_label: str = "unknown",
) -> Dict[str, Any]:
    """Lightweight goal snapshot for Table 5 — local JSON mirror."""
    from trading_ai.nte.databank.local_trade_store import databank_memory_root

    contrib = {"A": 0.0, "B": 0.0, "C": 0.0}
    now = datetime.now(timezone.utc)
    for ev in events:
        ts = _parse_close_ts(ev.get("timestamp_close"))
        if ts is None:
            continue
        ts = ts.astimezone(timezone.utc)
        if (now - ts).total_seconds() > 30 * 86400:
            continue
        aid = str(ev.get("avenue_id") or "")
        if aid in contrib:
            contrib[aid] += _num(ev.get("net_pnl"))

    rolling_7d = sum(_num(e.get("net_pnl")) for e in events if _in_last_days(e, 7))
    rolling_30d = sum(_num(e.get("net_pnl")) for e in events if _in_last_days(e, 30))

    snap = {
        "snapshot_id": f"goal:{now.strftime('%Y%m%dT%H%M%SZ')}",
        "active_goal": active_goal,
        "current_equity": current_equity,
        "rolling_7d_net_profit": round(rolling_7d, 6),
        "rolling_30d_net_profit": round(rolling_30d, 6),
        "avenue_a_contribution": round(contrib["A"], 6),
        "avenue_b_contribution": round(contrib["B"], 6),
        "avenue_c_contribution": round(contrib["C"], 6),
        "current_speed_label": speed_label,
        "blockers": [],
        "top_actions": [],
        "created_at": now.isoformat(),
        "schema_version": DATABANK_SCHEMA_VERSION,
    }
    path = databank_memory_root() / "goal_progress_snapshot.json"
    save_aggregate(path, {"latest": snap, "updated": now.isoformat()})
    return snap


def _in_last_days(ev: Mapping[str, Any], days: int) -> bool:
    ts = _parse_close_ts(ev.get("timestamp_close"))
    if ts is None:
        return False
    ts = ts.astimezone(timezone.utc)
    delta = datetime.now(timezone.utc) - ts
    return 0 <= delta.total_seconds() <= days * 86400


def update_ceo_review_snapshot(events: List[Mapping[str, Any]], *, review_type: str = "adhoc") -> Dict[str, Any]:
    """Table 6 mirror — local JSON for CEO sessions."""
    from trading_ai.nte.databank.local_trade_store import databank_memory_root

    comp = {}
    for e in events:
        aid = str(e.get("avenue_id") or "?")
        comp.setdefault(aid, 0.0)
        comp[aid] += _num(e.get("net_pnl"))
    best_avenue = max(comp, key=lambda k: comp[k]) if comp else None
    weakest_avenue = min(comp, key=lambda k: comp[k]) if comp else None
    strat: Dict[str, float] = {}
    for e in events:
        s = str(e.get("strategy_id") or "unknown")
        strat[s] = strat.get(s, 0.0) + _num(e.get("net_pnl"))
    best_strategy = max(strat, key=lambda k: strat[k]) if strat else None
    weakest_strategy = min(strat, key=lambda k: strat[k]) if strat else None
    now = datetime.now(timezone.utc).isoformat()
    snap = {
        "ceo_snapshot_id": f"ceo:{review_type}:{now}",
        "review_type": review_type,
        "active_goal": "global",
        "best_avenue": best_avenue,
        "weakest_avenue": weakest_avenue,
        "best_strategy": best_strategy,
        "weakest_strategy": weakest_strategy,
        "top_actions": [],
        "top_risks": [],
        "top_research_priorities": [],
        "open_action_count": 0,
        "created_at": now,
        "schema_version": DATABANK_SCHEMA_VERSION,
    }
    path = databank_memory_root() / "ceo_review_snapshot.json"
    save_aggregate(path, {"latest": snap, "updated": now})
    return snap


def append_learning_hook(event: Mapping[str, Any], scores: Mapping[str, Any]) -> None:
    from trading_ai.nte.databank.local_trade_store import databank_memory_root

    path = databank_memory_root() / "research_learning_hooks.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "trade_id": event.get("trade_id"),
        "avenue_id": event.get("avenue_id"),
        "strategy_id": event.get("strategy_id"),
        "trade_quality_score": scores.get("trade_quality_score"),
        "execution_score": scores.get("execution_score"),
        "edge_score": scores.get("edge_score"),
        "discipline_score": scores.get("discipline_score"),
        "timestamp_close": event.get("timestamp_close"),
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")
