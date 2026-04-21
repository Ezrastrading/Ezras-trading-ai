"""Append-only raw trade logs + daily/weekly summaries + human-readable report."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.intelligence.edge_performance import drawdown_ok, max_drawdown
from trading_ai.nte.utils.atomic_json import atomic_write_json
from trading_ai.reality.paths import trade_logs_dir


def trades_raw_path(base: Optional[Path] = None) -> Path:
    return (base or trade_logs_dir()) / "trades_raw.jsonl"


def daily_summary_path(base: Optional[Path] = None) -> Path:
    return (base or trade_logs_dir()) / "daily_summary.json"


def weekly_summary_path(base: Optional[Path] = None) -> Path:
    return (base or trade_logs_dir()) / "weekly_summary.json"


def human_report_path(base: Optional[Path] = None) -> Path:
    return (base or trade_logs_dir()) / "human_report.txt"


def milestone_state_path(base: Optional[Path] = None) -> Path:
    return (base or trade_logs_dir()) / "milestone_state.json"


def _parse_day(ts: str) -> str:
    s = (ts or "").strip()
    if not s:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if "T" in s:
        return s.split("T", 1)[0][:10]
    return s[:10]


def _parse_week_key(ts: str) -> str:
    try:
        if "T" in ts:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(ts[:10])
        y, w, _ = dt.isocalendar()
        return f"{y}-W{w:02d}"
    except (ValueError, TypeError):
        now = datetime.now(timezone.utc)
        y, w, _ = now.isocalendar()
        return f"{y}-W{w:02d}"


def _load_json_dict(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def milestone_verdict(
    *,
    trade_count: int,
    cumulative_net_pnl: float,
    net_expectancy: float,
    drawdown_acceptable: bool,
) -> str:
    """20 / 50 / 100 trade gates (measurement labels)."""
    if trade_count < 20:
        return "INSUFFICIENT_SAMPLE"
    if trade_count >= 100:
        if net_expectancy > 0 and drawdown_acceptable:
            return "REAL_EDGE_CONFIRMED"
        if net_expectancy <= 0:
            return "FALSE_EDGE"
        if not drawdown_acceptable:
            return "REAL_EDGE_PENDING_DRAWDOWN"
        return "INSUFFICIENT_SAMPLE"
    if trade_count >= 50 and net_expectancy <= 0:
        return "FALSE_EDGE"
    if cumulative_net_pnl <= 0:
        return "NO_EDGE"
    return "INSUFFICIENT_SAMPLE"


def _read_all_raw(base: Path) -> List[Dict[str, Any]]:
    p = trades_raw_path(base)
    if not p.is_file():
        return []
    out: List[Dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _rebuild_summaries_from_raw(base: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    rows = _read_all_raw(base)
    daily: Dict[str, Any] = {}
    weekly: Dict[str, Any] = {}
    for r in rows:
        ts = str(r.get("timestamp") or "")
        day = _parse_day(ts)
        wk = _parse_week_key(ts)
        net = float(r.get("net_pnl") or 0.0)
        gross = float(r.get("gross_pnl") or 0.0)
        fees = float(r.get("fees") or 0.0)
        edge_id = str(r.get("edge_id") or "unknown")
        d = daily.setdefault(
            day,
            {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "gross_pnl": 0.0,
                "net_pnl": 0.0,
                "fees_total": 0.0,
                "best_trade": None,
                "worst_trade": None,
                "discipline_scores": [],
            },
        )
        d["total_trades"] += 1
        if net > 0:
            d["wins"] += 1
        else:
            d["losses"] += 1
        d["gross_pnl"] += gross
        d["net_pnl"] += net
        d["fees_total"] += fees
        if d["best_trade"] is None or net > float(d["best_trade"]):
            d["best_trade"] = net
        if d["worst_trade"] is None or net < float(d["worst_trade"]):
            d["worst_trade"] = net
        ds = r.get("discipline_score")
        if ds is not None:
            d["discipline_scores"].append(float(ds))

        w = weekly.setdefault(
            wk,
            {
                "total_trades": 0,
                "net_pnl": 0.0,
                "fees_total": 0.0,
                "by_edge": {},
                "_days": set(),
                "ordered_net": [],
            },
        )
        w["total_trades"] += 1
        w["net_pnl"] += net
        w["fees_total"] += fees
        w["_days"].add(day)
        be = w["by_edge"].setdefault(edge_id, 0.0)
        w["by_edge"][edge_id] = float(be) + net
        w["ordered_net"].append(net)

    for day, blob in daily.items():
        n = int(blob["total_trades"])
        blob["win_rate"] = blob["wins"] / n if n else 0.0
        blob["avg_trade_pnl"] = blob["net_pnl"] / n if n else 0.0
        scores = blob.pop("discipline_scores", [])
        blob["avg_discipline_score"] = sum(scores) / len(scores) if scores else None

    for wk, blob in weekly.items():
        days = sorted(blob.pop("_days", set()))
        blob["days"] = days
        blob["day_count"] = len(days)
        nets = blob.pop("ordered_net")
        blob["avg_daily_pnl"] = blob["net_pnl"] / len(days) if days else 0.0
        curve: List[float] = []
        acc = 0.0
        for x in nets:
            acc += float(x)
            curve.append(acc)
        blob["max_drawdown"] = max_drawdown(curve) if curve else 0.0
        be = blob.get("by_edge") or {}
        if be:
            best_edge = max(be.items(), key=lambda kv: kv[1])
            worst_edge = min(be.items(), key=lambda kv: kv[1])
            blob["best_edge"] = {"edge_id": best_edge[0], "net_pnl": best_edge[1]}
            blob["worst_edge"] = {"edge_id": worst_edge[0], "net_pnl": worst_edge[1]}
        else:
            blob["best_edge"] = None
            blob["worst_edge"] = None
    return daily, weekly


def _overall_execution_quality(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "GOOD"
    recent = rows[-50:]
    bad = sum(1 for r in recent if str(r.get("execution_flag") or "") == "EXECUTION_KILLING_EDGE")
    return "POOR" if bad >= max(3, len(recent) // 10) else "GOOD"


def _write_human_report(base: Path, daily: Dict[str, Any], weekly: Dict[str, Any]) -> None:
    rows = _read_all_raw(base)
    if not rows:
        human_report_path(base).write_text(
            "(no trades logged yet)\n",
            encoding="utf-8",
        )
        return
    last = rows[-1]
    day = _parse_day(str(last.get("timestamp") or ""))
    d = daily.get(day) or {}
    trades = int(d.get("total_trades") or 0)
    wr = float(d.get("win_rate") or 0.0)
    net = float(d.get("net_pnl") or 0.0)
    fees = float(d.get("fees_total") or 0.0)
    avg_disc = d.get("avg_discipline_score")
    conf = str(last.get("confidence_level") or "LOW")
    top = None
    worst = None
    for r in rows:
        if _parse_day(str(r.get("timestamp") or "")) != day:
            continue
        eid = str(r.get("edge_id") or "")
        p = float(r.get("net_pnl") or 0.0)
        top = (eid, p) if top is None or p > top[1] else top
        worst = (eid, p) if worst is None or p < worst[1] else worst
    exec_q = _overall_execution_quality(rows)
    lines = [
        f"Day: {day}",
        "",
        f"Trades: {trades}",
        f"Win Rate: {wr * 100:.0f}%",
        f"Net PnL: {net:+.2f}",
        f"Fees: {fees:.2f}",
        "",
    ]
    if top and worst:
        lines.append(f"Top Edge: {top[0]} ({top[1]:+.2f})")
        lines.append(f"Worst Edge: {worst[0]} ({worst[1]:+.2f})")
        lines.append("")
    lines.append(f"Execution Quality: {exec_q}")
    if avg_disc is not None:
        lines.append(f"Discipline: {avg_disc:.0f}/100")
    lines.append(f"Confidence: {conf}")
    lines.append("")
    lines.append("Verdict:")
    if net > 0 and exec_q == "GOOD":
        lines.append("System profitable after fees. Continue.")
    elif net <= 0:
        lines.append("Net PnL non-positive for the day — review.")
    else:
        lines.append("Mixed — review execution and fees.")
    lines.append("")
    wk = _parse_week_key(str(last.get("timestamp") or ""))
    w = weekly.get(wk) or {}
    lines.append(f"Week ({wk}): trades={w.get('total_trades', 0)}, net={float(w.get('net_pnl') or 0):+.2f}")
    human_report_path(base).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _update_milestones(base: Path, rows: List[Dict[str, Any]]) -> None:
    nets = [float(r.get("net_pnl") or 0.0) for r in rows]
    n = len(nets)
    cum = sum(nets)
    exp = 0.0
    if nets:
        wins = [x for x in nets if x > 0]
        losses = [x for x in nets if x <= 0]
        wr = len(wins) / n
        aw = sum(wins) / len(wins) if wins else 0.0
        al = sum(abs(x) for x in losses) / len(losses) if losses else 0.0
        exp = (wr * aw) - ((1.0 - wr) * al)
    curve: List[float] = []
    acc = 0.0
    for x in nets:
        acc += float(x)
        curve.append(acc)
    dd_ok = drawdown_ok(curve, limit=0.1) if len(curve) >= 2 else False
    label = milestone_verdict(
        trade_count=n,
        cumulative_net_pnl=cum,
        net_expectancy=exp,
        drawdown_acceptable=dd_ok,
    )
    payload = {
        "trade_count": n,
        "cumulative_net_pnl": cum,
        "net_expectancy": exp,
        "drawdown_acceptable": dd_ok,
        "milestone_verdict": label,
    }
    atomic_write_json(milestone_state_path(base), payload)


def append_trade_record(
    record: Dict[str, Any],
    *,
    base: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Append one trade to ``trades_raw.jsonl`` (never delete), rebuild summaries + report.
    """
    b = base or trade_logs_dir()
    b.mkdir(parents=True, exist_ok=True)
    p = trades_raw_path(b)
    line = json.dumps(record, default=str) + "\n"
    with p.open("a", encoding="utf-8") as f:
        f.write(line)
    rows = _read_all_raw(b)
    daily, weekly = _rebuild_summaries_from_raw(b)
    atomic_write_json(daily_summary_path(b), daily)
    atomic_write_json(weekly_summary_path(b), weekly)
    _write_human_report(b, daily, weekly)
    _update_milestones(b, rows)
    return {
        "daily_keys": list(daily.keys()),
        "weekly_keys": list(weekly.keys()),
        "milestone": _load_json_dict(milestone_state_path(b)),
    }
