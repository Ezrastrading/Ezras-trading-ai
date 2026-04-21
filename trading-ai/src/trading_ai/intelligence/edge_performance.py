"""Aggregate edge / system PnL metrics for profit-reality validation (measurement only)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


MIN_TRADES_REQUIRED = 30


@dataclass
class EdgePerformance:
    trades: int = 0
    wins: int = 0
    losses: int = 0
    net_pnl: float = 0.0
    total_fees: float = 0.0
    total_slippage: float = 0.0

    def record_trade(self, pnl: float, fees: float, slippage: float) -> None:
        self.trades += 1
        self.net_pnl += float(pnl)
        self.total_fees += float(fees)
        self.total_slippage += float(slippage)
        if pnl > 0:
            self.wins += 1
        else:
            self.losses += 1

    def win_rate(self) -> float:
        return self.wins / max(self.trades, 1)

    def avg_pnl(self) -> float:
        return self.net_pnl / max(self.trades, 1)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trades": self.trades,
            "wins": self.wins,
            "losses": self.losses,
            "net_pnl": self.net_pnl,
            "total_fees": self.total_fees,
            "total_slippage": self.total_slippage,
        }

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "EdgePerformance":
        if not d:
            return cls()
        return cls(
            trades=int(d.get("trades") or 0),
            wins=int(d.get("wins") or 0),
            losses=int(d.get("losses") or 0),
            net_pnl=float(d.get("net_pnl") or 0.0),
            total_fees=float(d.get("total_fees") or 0.0),
            total_slippage=float(d.get("total_slippage") or 0.0),
        )


def is_edge_proven(edge_perf: EdgePerformance) -> bool:
    return edge_perf.trades >= MIN_TRADES_REQUIRED


def calculate_expectancy(edge_perf: EdgePerformance) -> float:
    avg_win = edge_perf.net_pnl / max(edge_perf.wins, 1)
    avg_loss = abs(edge_perf.net_pnl / max(edge_perf.losses, 1))
    win_rate = edge_perf.win_rate()
    return float((avg_win * win_rate) - (avg_loss * (1.0 - win_rate)))


def real_net_profit(edge_perf: EdgePerformance) -> float:
    return float(edge_perf.net_pnl - edge_perf.total_fees - edge_perf.total_slippage)


def is_profitable(edge_perf: EdgePerformance) -> bool:
    return real_net_profit(edge_perf) > 0


def is_consistent(trade_pnls: List[float]) -> bool:
    import statistics

    if len(trade_pnls) < 10:
        return False
    std_dev = statistics.stdev(trade_pnls)
    mean_abs = abs(sum(trade_pnls) / len(trade_pnls))
    if mean_abs < 1e-12:
        return std_dev <= 1e-9
    return not (std_dev > mean_abs * 2.0)


def max_drawdown(equity_curve: List[float]) -> float:
    if not equity_curve:
        return 0.0
    peak = float(equity_curve[0])
    max_dd = 0.0
    for val in equity_curve:
        v = float(val)
        if v > peak:
            peak = v
        dd = peak - v
        if dd > max_dd:
            max_dd = dd
    return max_dd


def drawdown_ok(equity_curve: List[float], limit: float = 0.1) -> bool:
    """``limit`` is a fraction of peak equity (e.g. 0.1 = 10% max drawdown vs running peak)."""
    if not equity_curve:
        return False
    peak = max(float(x) for x in equity_curve)
    if peak <= 0:
        return False
    return (max_drawdown(equity_curve) / peak) <= float(limit)


def slippage_ratio(edge_perf: EdgePerformance) -> float:
    return edge_perf.total_slippage / max(edge_perf.net_pnl, 1e-6)


def latency_safe(latency_ms: float, threshold: float = 2000.0) -> bool:
    return float(latency_ms) < float(threshold)
