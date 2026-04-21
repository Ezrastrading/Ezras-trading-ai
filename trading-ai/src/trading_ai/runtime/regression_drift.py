"""
Runtime regression / drift analysis from trade rows (sim or federated).

Writes ``data/control/regression_drift.json`` and may emit ``regression::investigate`` shadow tasks.
"""

from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.runtime_paths import ezras_runtime_root


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _net(t: Dict[str, Any]) -> Optional[float]:
    for k in ("net_pnl_usd", "net_pnl"):
        v = t.get(k)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _closed_rows(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for t in trades:
        if not isinstance(t, dict):
            continue
        st = str(t.get("status") or "").lower()
        if st in ("closed", "settled", "filled"):
            out.append(t)
    return out


def _metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    nets = [_net(t) for t in rows]
    vals = [n for n in nets if n is not None]
    if not vals:
        return {"count": 0, "total_pnl": 0.0, "win_rate": None, "volatility": None}
    wins = sum(1 for n in vals if n > 0)
    vol = None
    if len(vals) >= 3:
        try:
            vol = float(statistics.pstdev(vals))
        except statistics.StatisticsError:
            vol = None
    return {
        "count": len(vals),
        "total_pnl": round(sum(vals), 6),
        "win_rate": round(wins / len(vals), 4) if vals else None,
        "volatility": None if vol is None else round(vol, 6),
    }


def analyze_trades_for_drift(
    trades: List[Dict[str, Any]],
    *,
    recent_n: int = 20,
    baseline_n: int = 20,
) -> Dict[str, Any]:
    rows = _closed_rows(trades)
    if len(rows) < recent_n + baseline_n:
        return {
            "truth_version": "regression_drift_v1",
            "generated_at": _iso(),
            "verdict": "insufficient_history",
            "emit_investigate_tasks": False,
            "recent": {},
            "baseline": {},
            "reason": f"need>={recent_n + baseline_n} closed rows got={len(rows)}",
        }
    # chronological: assume list oldest->newest; baseline older segment, recent tail
    baseline_rows = rows[-(recent_n + baseline_n) : -recent_n]
    recent_rows = rows[-recent_n:]
    bm = _metrics(baseline_rows)
    rm = _metrics(recent_rows)
    verdict = "stable"
    emit = False
    reasons: List[str] = []

    if bm.get("count") and rm.get("count"):
        if rm["total_pnl"] < bm["total_pnl"] - 5.0:
            verdict = "pnl_drop"
            emit = True
            reasons.append("recent_total_pnl_below_baseline")
        br = bm.get("win_rate")
        rr = rm.get("win_rate")
        if br is not None and rr is not None and rr < br - 0.12:
            verdict = "win_rate_drop" if verdict == "stable" else verdict
            emit = True
            reasons.append("win_rate_degraded")
        bv = bm.get("volatility")
        rv = rm.get("volatility")
        if bv and rv and rv > bv * 1.65:
            verdict = "volatility_spike" if verdict == "stable" else verdict
            emit = True
            reasons.append("volatility_spike")

    return {
        "truth_version": "regression_drift_v1",
        "generated_at": _iso(),
        "verdict": verdict,
        "emit_investigate_tasks": emit,
        "recent": rm,
        "baseline": bm,
        "signals": reasons,
        "honesty": "Heuristic window compare on closed trade rows only; advisory routing.",
    }


def analyze_and_write_regression_drift(
    *,
    runtime_root: Optional[Path] = None,
    trades: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Load trades from ``sim_trade_log.json`` when ``trades`` not provided; write control artifact + tasks.
    """
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = root / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)

    use_trades: List[Dict[str, Any]] = list(trades or [])
    if len(_closed_rows(use_trades)) < 40:
        p = ctrl / "sim_trade_log.json"
        if p.is_file():
            try:
                doc = json.loads(p.read_text(encoding="utf-8"))
                xs = doc.get("trades") or []
                alt = [x for x in xs if isinstance(x, dict)]
                if len(_closed_rows(alt)) >= len(_closed_rows(use_trades)):
                    use_trades = alt
            except (OSError, json.JSONDecodeError):
                pass

    analysis = analyze_trades_for_drift(list(use_trades or []))
    out_p = ctrl / "regression_drift.json"
    tmp = out_p.with_suffix(".tmp")
    tmp.write_text(json.dumps(analysis, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(out_p)

    if analysis.get("emit_investigate_tasks"):
        try:
            from trading_ai.global_layer.bot_registry import load_registry
            from trading_ai.global_layer.bot_types import BotRole
            from trading_ai.global_layer.task_router import route_task_shadow

            reg = load_registry()
            scopes = {
                (str(b.get("avenue") or "A"), str(b.get("gate") or "none"))
                for b in (reg.get("bots") or [])
                if isinstance(b, dict)
            }
            scopes = scopes or {("A", "none")}
            for av, gate in scopes:
                t = route_task_shadow(
                    avenue=av,
                    gate=gate,
                    task_type="regression::investigate",
                    source_bot_id="runtime_regression_drift",
                    role=BotRole.LEARNING.value,
                    evidence_ref=str(out_p),
                )
                t["priority"] = int(t.get("priority") or 0) + 300
        except Exception:
            pass

    return analysis
