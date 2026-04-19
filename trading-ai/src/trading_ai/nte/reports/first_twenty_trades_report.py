"""
First 20 closed trades — diagnosis phase (clean execution, edge signal, control).

Produces structured dict + markdown suitable for CEO / ops review.
"""

from __future__ import annotations

import json
from statistics import mean
from typing import Any, Dict, List, Optional

from trading_ai.nte.memory.store import MemoryStore
from trading_ai.nte.monitoring.execution_counters import load_counters
from trading_ai.nte.monitoring.live_dashboard import strategy_ab_label


def _coinbase_trades(store: Optional[MemoryStore] = None) -> List[Dict[str, Any]]:
    st = store or MemoryStore()
    st.ensure_defaults()
    tm = st.load_json("trade_memory.json")
    trades = [t for t in (tm.get("trades") or []) if isinstance(t, dict)]
    return [t for t in trades if str(t.get("avenue") or t.get("avenue_id") or "") == "coinbase"]


def build_first_twenty_trades_report(*, store: Optional[MemoryStore] = None) -> Dict[str, Any]:
    all_cb = _coinbase_trades(store)
    n = min(20, len(all_cb))
    last = all_cb[-n:] if n else []

    rows: List[Dict[str, Any]] = []
    for t in last:
        setup = str(t.get("setup_type") or "")
        rows.append(
            {
                "timestamp": t.get("logged_at") or t.get("ts"),
                "asset": t.get("asset") or t.get("product_id"),
                "strategy_ab": strategy_ab_label(setup),
                "setup_type": setup,
                "entry_price": t.get("entry_price"),
                "exit_price": t.get("exit_price"),
                "base_size": t.get("base_size"),
                "spread_bps": t.get("spread_bps"),
                "expected_edge_bps": t.get("expected_edge_bps"),
                "fees_usd": t.get("fees_usd") if t.get("fees_usd") is not None else t.get("fees"),
                "net_pnl_usd": t.get("net_pnl_usd"),
                "gross_pnl_usd": t.get("gross_pnl_usd"),
                "hold_sec": t.get("duration_sec"),
                "exit_reason": t.get("exit_reason"),
                "maker_intent": t.get("entry_maker_intent"),
                "entry_execution": t.get("entry_execution"),
                "realized_move_bps": t.get("realized_move_bps"),
                "regime": t.get("regime"),
            }
        )

    nets = [float(t.get("net_pnl_usd") or 0) for t in last]
    gross = [float(t.get("gross_pnl_usd") or t.get("net_pnl_usd") or 0) for t in last]
    fees = [float(t.get("fees_usd") or t.get("fees") or 0) for t in last]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    holds = [float(t.get("duration_sec") or 0) for t in last]

    maker_flags = [t.get("entry_maker_intent") for t in last]
    maker_yes = sum(1 for m in maker_flags if m is True)
    takerish = sum(1 for m in maker_flags if m is False)

    def bucket_ab(label: str) -> List[Dict[str, Any]]:
        return [t for t in last if strategy_ab_label(str(t.get("setup_type") or "")) == label]

    def strat_stats(label: str) -> Dict[str, Any]:
        sub = [t for t in last if strategy_ab_label(str(t.get("setup_type") or "")) == label]
        sn = [float(t.get("net_pnl_usd") or 0) for t in sub]
        sw = [x for x in sn if x > 0]
        return {
            "trades": len(sub),
            "win_rate": (len(sw) / len(sub)) if sub else 0.0,
            "net_pnl_usd": sum(sn),
        }

    ctr = load_counters()
    placed = max(1, int(ctr.get("limit_entries_placed") or 0))
    filled = int(ctr.get("limit_entries_filled") or 0)
    stale_c = int(ctr.get("stale_pending_canceled") or 0)

    spreads = []
    for t in last:
        sb = t.get("spread_bps")
        if sb is not None:
            try:
                spreads.append(float(sb))
            except (TypeError, ValueError):
                pass
        elif t.get("spread") is not None:
            try:
                spreads.append(float(t.get("spread")) * 10000.0)
            except (TypeError, ValueError):
                pass

    observations = []
    if n < 20:
        observations.append(f"Only {n} Coinbase trades logged so far — keep going until 20.")
    if fees and sum(fees) > abs(sum(nets)) * 0.4 and sum(nets) <= 0:
        observations.append("Fees are a large share of PnL — check maker ratio and size.")
    if stale_c / float(placed) > 0.35:
        observations.append("High stale cancel rate — entry may be too passive or regime shifted.")

    return {
        "schema_version": 1,
        "diagnosis_phase": True,
        "target_trades": 20,
        "trades_included": n,
        "trade_table": rows,
        "summary_metrics": {
            "total_trades": n,
            "win_rate_pct": (100.0 * len(wins) / n) if n else 0.0,
            "avg_win_usd": mean(wins) if wins else 0.0,
            "avg_loss_usd": mean(losses) if losses else 0.0,
            "total_fees_usd": sum(fees),
            "net_pnl_usd": sum(nets),
            "gross_pnl_usd": sum(gross),
            "avg_hold_sec": mean(holds) if holds else 0.0,
            "maker_intent_pct": (100.0 * maker_yes / n) if n else 0.0,
            "taker_or_market_entry_pct": (100.0 * takerish / n) if n else 0.0,
            "cancel_rate_pct": 100.0 * stale_c / float(placed),
        },
        "strategy_breakdown": {
            "A_mean_reversion": strat_stats("A"),
            "B_continuation_pullback": strat_stats("B"),
        },
        "execution_stats": {
            "avg_spread_bps": mean(spreads) if spreads else None,
            "avg_volatility_z": mean([float(t.get("volatility") or 0) for t in last]) if last else None,
            "pending_fill_ratio": filled / float(placed) if placed else 0.0,
            "cancel_rate": stale_c / float(placed) if placed else 0.0,
            "avg_fill_time_note": "Fill time requires per-order timestamps (extend pending→fill).",
        },
        "observations": observations,
    }


def first_twenty_trades_markdown(store: Optional[MemoryStore] = None) -> str:
    r = build_first_twenty_trades_report(store=store)
    lines = [
        "# First 20 trades — diagnosis",
        "",
        f"**Trades included:** {r['trades_included']} / {r['target_trades']}",
        "",
        "## Summary",
        "",
        "```",
        json.dumps(r["summary_metrics"], indent=2),
        "```",
        "",
        "## Strategy A vs B",
        "",
        "```",
        json.dumps(r["strategy_breakdown"], indent=2),
        "```",
        "",
        "## Execution",
        "",
        "```",
        json.dumps(r["execution_stats"], indent=2),
        "```",
        "",
        "## Observations",
        "",
    ]
    for o in r.get("observations") or []:
        lines.append(f"- {o}")
    if not r.get("observations"):
        lines.append("- (none)")
    lines.append("")
    return "\n".join(lines)
