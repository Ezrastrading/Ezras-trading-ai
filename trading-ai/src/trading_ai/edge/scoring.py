"""Compute per-edge metrics from closed trade events."""

from __future__ import annotations

import math
import statistics
from typing import Any, Dict, List, Mapping, Optional, Sequence

from trading_ai.edge.models import EdgeTradeMetrics


def _net_pnl(ev: Mapping[str, Any]) -> float:
    try:
        return float(ev.get("net_pnl") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _fees(ev: Mapping[str, Any]) -> float:
    try:
        return float(ev.get("fees_paid") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def trades_for_edge(events: Sequence[Mapping[str, Any]], edge_id: str) -> List[Dict[str, Any]]:
    eid = (edge_id or "").strip()
    if not eid:
        return []
    out: List[Dict[str, Any]] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if str(ev.get("edge_id") or "").strip() == eid:
            out.append(dict(ev))
    return out


def compute_edge_metrics(
    events: Sequence[Mapping[str, Any]],
    edge_id: str,
    *,
    fee_field_for_expectancy: str = "post_fee",
) -> EdgeTradeMetrics:
    """
    Core expectancy (pre-fee from price path): E = win_rate * avg_win - loss_rate * avg_loss
    on **gross** move; post_fee_expectancy subtracts per-trade fees from each outcome net.

    ``fee_field_for_expectancy``:
    - ``post_fee``: use net_pnl only for expectancy (aligned with "after fees" mandate).
    - ``split``: classical formula on gross pnl minus avg fee per trade (informational).
    """
    rows = trades_for_edge(events, edge_id)
    pnls = [_net_pnl(r) for r in rows]
    fees = [_fees(r) for r in rows]
    gross_like = [pnls[i] + fees[i] for i in range(len(pnls))]

    n = len(pnls)
    if n == 0:
        return EdgeTradeMetrics(
            edge_id=edge_id,
            total_trades=0,
            wins=0,
            losses=0,
            win_rate=0.0,
            avg_win=0.0,
            avg_loss=0.0,
            expectancy=0.0,
            post_fee_expectancy=0.0,
            net_pnl=0.0,
            pnl_per_trade=0.0,
            gross_fees=0.0,
            max_drawdown=0.0,
            variance_pnl=0.0,
            stability_score=0.0,
            sample_net_pnls=[],
        )

    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p < 0]
    flat = len(pnls) - len(wins) - len(losses)
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = win_count / n if n else 0.0
    loss_rate = loss_count / n if n else 0.0

    avg_win = statistics.mean(wins) if wins else 0.0
    avg_loss = statistics.mean(losses) if losses else 0.0

    # Classical expectancy on gross PnL components
    expectancy_gross = (win_rate * avg_win) - (loss_rate * avg_loss) if n else 0.0

    # Post-fee: primary = mean(net_pnl) == empirical post-fee expectancy per trade
    net_sum = sum(pnls)
    post_fee_expectancy = net_sum / n
    avg_fee = sum(fees) / n if n else 0.0

    if fee_field_for_expectancy == "post_fee":
        expectancy_report = post_fee_expectancy
    else:
        expectancy_report = expectancy_gross - avg_fee

    # Drawdown on cumulative net pnl curve
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    var_pnl = statistics.pvariance(pnls) if n > 1 else 0.0
    std_pnl = math.sqrt(var_pnl) if var_pnl > 0 else 0.0
    # Higher when mean positive and std low
    stability = (post_fee_expectancy / (1.0 + std_pnl)) if std_pnl >= 0 else post_fee_expectancy

    return EdgeTradeMetrics(
        edge_id=edge_id,
        total_trades=n,
        wins=win_count,
        losses=loss_count,
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        expectancy=expectancy_gross,
        post_fee_expectancy=post_fee_expectancy,
        net_pnl=net_sum,
        pnl_per_trade=net_sum / n,
        gross_fees=sum(fees),
        max_drawdown=max_dd,
        variance_pnl=var_pnl,
        stability_score=stability,
        sample_net_pnls=list(pnls),
    )


def metrics_to_dict(m: EdgeTradeMetrics) -> Dict[str, Any]:
    return {
        "edge_id": m.edge_id,
        "total_trades": m.total_trades,
        "win_rate": m.win_rate,
        "avg_win": m.avg_win,
        "avg_loss": m.avg_loss,
        "expectancy": m.expectancy,
        "post_fee_expectancy": m.post_fee_expectancy,
        "net_pnl": m.net_pnl,
        "pnl_per_trade": m.pnl_per_trade,
        "gross_fees": m.gross_fees,
        "max_drawdown": m.max_drawdown,
        "variance_pnl": m.variance_pnl,
        "stability_score": m.stability_score,
    }
