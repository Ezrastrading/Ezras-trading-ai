"""
CEO-facing Gate A / Gate B / global reports — PnL, win rate, execution quality, risk.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional


def build_gate_a_report(metrics: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    m = dict(metrics or {})
    return {
        "report": "gate_a",
        "total_trades": m.get("total_trades"),
        "win_rate": m.get("win_rate"),
        "avg_net_pnl_usd": m.get("avg_net_pnl_usd"),
        "execution_quality_avg": m.get("execution_quality_avg"),
        "max_drawdown_usd": m.get("max_drawdown_usd"),
        "slippage_aware": True,
        "crypto_balance_aware": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def build_gate_b_report(metrics: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    m = dict(metrics or {})
    return {
        "report": "gate_b",
        "total_trades": m.get("total_trades"),
        "win_rate": m.get("win_rate"),
        "avg_net_pnl_usd": m.get("avg_net_pnl_usd"),
        "avg_execution_quality": m.get("avg_execution_quality"),
        "theoretical_vs_actual_drag": m.get("theoretical_vs_actual_drag"),
        "max_drawdown_usd": m.get("max_drawdown_usd"),
        "liquidity_reject_count": m.get("liquidity_reject_count"),
        "fake_breakout_reject_count": m.get("fake_breakout_reject_count"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def build_global_gate_report(
    *,
    gate_a: Optional[Mapping[str, Any]] = None,
    gate_b: Optional[Mapping[str, Any]] = None,
    capital_total_usd: Optional[float] = None,
    evolution_summary: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "report": "global_gates",
        "capital_total_usd": capital_total_usd,
        "gate_a": build_gate_a_report(gate_a),
        "gate_b": build_gate_b_report(gate_b),
        "comparison_notes": [
            "Compare post-fee expectancy and execution_quality across gates on the same clock.",
            "Gate B should show controlled drawdowns vs Gate A baseline volatility.",
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if evolution_summary:
        out["evolution_loop_summary"] = dict(evolution_summary)
        out["gate_a_vs_gate_b_evidence"] = (
            evolution_summary.get("gate_split") if isinstance(evolution_summary, dict) else None
        )
    return out
