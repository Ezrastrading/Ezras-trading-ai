"""Per-gate (Gate A vs Gate B) aggregates from trade_events — measured fields only."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Optional

from trading_ai.nte.databank.local_trade_store import load_all_trade_events


def _row_mentions_gate(row: Dict[str, Any], gate: str) -> bool:
    g = gate.lower().strip()
    blob = json.dumps(row, default=str).lower()
    if g in blob:
        return True
    for k in ("strategy_id", "route_chosen", "execution_profile", "adaptive_scope"):
        if g in str(row.get(k) or "").lower():
            return True
    msj = row.get("market_snapshot_json")
    if isinstance(msj, str) and g in msj.lower():
        return True
    if isinstance(msj, dict) and g in json.dumps(msj).lower():
        return True
    return False


def _num(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def aggregate_coinbase_gate_metrics(
    rows: Optional[List[Dict[str, Any]]] = None,
    *,
    gate: str,
) -> Dict[str, Any]:
    """
    Filter events that appear tied to ``gate_a`` or ``gate_b`` (best-effort string match).

    No fabricated metrics — empty lists yield zeros and explicit honesty note.
    """
    all_rows = rows if rows is not None else load_all_trade_events()
    filt = [r for r in all_rows if isinstance(r, dict) and _row_mentions_gate(r, gate)]
    pnls: List[float] = []
    holds: List[float] = []
    wins = 0
    for r in filt:
        p = _num(r.get("net_pnl_usd"))
        if p is None:
            p = _num(r.get("net_pnl"))
        if p is not None:
            pnls.append(p)
            if p > 0:
                wins += 1
        h = _num(r.get("hold_seconds"))
        if h is not None:
            holds.append(h)
    n = len(pnls)
    wr = (wins / n) if n else 0.0
    exp = (sum(pnls) / n) if n else 0.0
    return {
        "truth_version": "coinbase_gate_metrics_v1",
        "gate": gate,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "trade_count_with_pnl": n,
        "win_rate": wr,
        "median_hold_sec": median(holds) if holds else None,
        "gross_pnl_sum": sum(pnls) if pnls else 0.0,
        "net_pnl_sum": sum(pnls) if pnls else 0.0,
        "expectancy_per_trade": exp,
        "honesty": "Gate attribution is heuristic (string match on record). Sparse or mis-tagged trades reduce accuracy.",
    }


def write_gate_scorecard_artifacts(*, runtime_root: Path) -> Dict[str, Any]:
    """Write ``databank/gate_a_scorecard.json`` and ``databank/gate_b_scorecard.json`` when databank root resolves."""
    root = Path(runtime_root).expanduser().resolve()
    out: Dict[str, Any] = {}
    try:
        ga = aggregate_coinbase_gate_metrics(gate="gate_a")
        gb = aggregate_coinbase_gate_metrics(gate="gate_b")
        db = root / "databank"
        db.mkdir(parents=True, exist_ok=True)
        (db / "gate_a_scorecard.json").write_text(json.dumps(ga, indent=2, default=str) + "\n", encoding="utf-8")
        (db / "gate_b_scorecard.json").write_text(json.dumps(gb, indent=2, default=str) + "\n", encoding="utf-8")
        out["gate_a_scorecard"] = ga
        out["gate_b_scorecard"] = gb
        out["written"] = True
    except Exception as exc:
        out["error"] = str(exc)
        out["written"] = False
    return out
