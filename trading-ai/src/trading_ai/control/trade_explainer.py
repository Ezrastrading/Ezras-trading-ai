"""Human-readable single-trade explanation for operators."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _grep_trade_id(path: Path, trade_id: str, max_lines: int = 8000) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    tid = str(trade_id).strip()
    out: List[Dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for ln in lines[-max_lines:]:
        ln = ln.strip()
        if tid not in ln:
            continue
        try:
            o = json.loads(ln)
            if isinstance(o, dict):
                out.append(o)
        except json.JSONDecodeError:
            continue
    return out


def _read_edge_summary() -> Dict[str, Any]:
    try:
        from trading_ai.reality.edge_truth import edge_truth_summary_path

        p = edge_truth_summary_path()
        if not p.is_file():
            return {}
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def explain_trade(trade_id: str) -> Path:
    """
    Write ``data/control/trade_explanations/{trade_id}.txt``.

    Returns path written (may be minimal if data missing).
    """
    from trading_ai.control.paths import trade_explanations_dir
    from trading_ai.reality.execution_truth import execution_truth_path
    from trading_ai.reality.trade_logger import trades_raw_path

    tid = str(trade_id).strip()
    out_dir = trade_explanations_dir()
    out_p = out_dir / f"{tid}.txt"
    raw_matches = _grep_trade_id(trades_raw_path(), tid)
    raw = raw_matches[-1] if raw_matches else {}
    ex_matches = _grep_trade_id(execution_truth_path(), tid)
    ex = ex_matches[-1] if ex_matches else {}
    edge = _read_edge_summary()

    prod = str(raw.get("product_id") or raw.get("asset") or ex.get("product_id") or "unknown")
    entry = raw.get("entry_price") or raw.get("entry") or "n/a"
    exit_ = raw.get("exit_price") or raw.get("exit") or "n/a"
    fees = raw.get("fees_usd") or raw.get("fees") or "n/a"
    net = raw.get("net_pnl_usd") or raw.get("net_pnl") or "n/a"
    exp_edge = ex.get("expected_edge_bps") or raw.get("expected_edge") or "n/a"

    lines = [
        f"TRADE: {prod}",
        f"ENTRY: {entry}",
        f"EXIT: {exit_}",
        "",
        "EXPECTED EDGE:",
        f"  {exp_edge}",
        "FEES:",
        f"  {fees}",
        "NET RESULT:",
        f"  {net}",
        "",
        "WHY TRADE HAPPENED:",
        f"- edge: {raw.get('edge_id') or raw.get('strategy_id') or 'n/a'}",
        f"- regime: {raw.get('regime') or ex.get('regime') or 'n/a'}",
        f"- confidence: {raw.get('confidence') or 'n/a'}",
        "",
        "WHAT WENT RIGHT:",
        "  (see net_pnl > 0 and execution_truth)",
        "",
        "WHAT WENT WRONG:",
        "  (see net_pnl < 0 and execution_truth)",
        "",
        "--- edge_truth_summary (excerpt) ---",
        json.dumps(edge, indent=2, default=str)[:4000],
    ]
    try:
        out_p.write_text("\n".join(lines), encoding="utf-8")
    except Exception as exc:
        logger.debug("explain_trade write: %s", exc)
        try:
            out_p.write_text(f"TRADE: {tid}\n(incomplete — error writing detail)\n", encoding="utf-8")
        except OSError:
            pass
    return out_p
