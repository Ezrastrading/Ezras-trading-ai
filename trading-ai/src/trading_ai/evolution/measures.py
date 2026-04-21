"""Real performance measurement from databank trade events — net-first, inspectable."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence

CapitalGate = Literal["gate_a", "gate_b", "unknown"]


def infer_capital_gate(ev: Mapping[str, Any]) -> CapitalGate:
    """Infer Coinbase Gate A vs Gate B from strategy / lane hints (best-effort)."""
    sid = str(ev.get("strategy_id") or "").lower()
    lane = str(ev.get("edge_lane") or "").lower()
    if "gate_b" in sid or "gate_b" in lane or lane == "gate_b_momentum":
        return "gate_b"
    aid = str(ev.get("avenue_id") or "").upper()
    an = str(ev.get("avenue_name") or "").lower()
    if aid == "A" or an == "coinbase":
        return "gate_a"
    return "unknown"


def _f(ev: Mapping[str, Any], *keys: str) -> float:
    for k in keys:
        if k in ev and ev[k] is not None:
            try:
                return float(ev[k])
            except (TypeError, ValueError):
                continue
    return 0.0


@dataclass
class TradeSliceMetrics:
    """Aggregates for a slice (edge, gate, avenue, or global)."""

    label: str
    n: int = 0
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    fees: float = 0.0
    slippage_usd: float = 0.0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    expectancy_net: float = 0.0
    max_drawdown: float = 0.0
    variance_pnl: float = 0.0
    profit_factor: float = 0.0
    sharpe_like: float = 0.0
    avg_hold_seconds: float = 0.0
    avg_latency_ms: float = 0.0
    avg_exec_quality: float = 0.0
    failure_or_error_rate: float = 0.0
    degraded_rate: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "n": self.n,
            "gross_pnl": self.gross_pnl,
            "net_pnl": self.net_pnl,
            "fees": self.fees,
            "slippage_usd": self.slippage_usd,
            "win_rate": self.win_rate,
            "avg_win": self.avg_win,
            "avg_loss": self.avg_loss,
            "expectancy_net": self.expectancy_net,
            "max_drawdown": self.max_drawdown,
            "variance_pnl": self.variance_pnl,
            "profit_factor": self.profit_factor,
            "sharpe_like": self.sharpe_like,
            "avg_hold_seconds": self.avg_hold_seconds,
            "avg_latency_ms": self.avg_latency_ms,
            "avg_execution_quality": self.avg_exec_quality,
            "failure_or_error_rate": self.failure_or_error_rate,
            "degraded_rate": self.degraded_rate,
        }


def _pnl_series(rows: Sequence[Mapping[str, Any]]) -> List[float]:
    out: List[float] = []
    for r in rows:
        out.append(_f(r, "net_pnl", "net_pnl_usd"))
    return out


def _max_dd(pnls: Sequence[float]) -> float:
    cum = 0.0
    peak = 0.0
    mdd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    return mdd


def compute_slice_metrics(label: str, rows: Sequence[Mapping[str, Any]]) -> TradeSliceMetrics:
    pnls = _pnl_series(rows)
    n = len(pnls)
    if n == 0:
        return TradeSliceMetrics(label=label)

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross = sum(_f(r, "gross_pnl") for r in rows)
    fees = sum(_f(r, "fees_paid", "fees_usd", "fees") for r in rows)
    slip = sum(
        abs(_f(r, "entry_slippage_bps")) + abs(_f(r, "exit_slippage_bps")) for r in rows
    )  # proxy bps sum; USD slippage optional
    for r in rows:
        slip += abs(_f(r, "slippage_usd", "total_slippage_usd"))

    win_rate = len(wins) / n
    avg_win = statistics.mean(wins) if wins else 0.0
    avg_loss = statistics.mean([abs(x) for x in losses]) if losses else 0.0
    net_sum = sum(pnls)
    exp = net_sum / n
    var_p = statistics.pvariance(pnls) if n > 1 else 0.0
    std = math.sqrt(var_p) if var_p > 0 else 0.0
    sharpe_like = (exp / std) if std > 1e-12 else exp

    gross_wins = sum(wins)
    gross_losses = abs(sum(losses))
    pf = (gross_wins / gross_losses) if gross_losses > 1e-12 else float("inf") if gross_wins > 0 else 0.0
    if math.isinf(pf):
        pf = 10.0

    holds = [_f(r, "hold_seconds") for r in rows if _f(r, "hold_seconds") > 0]
    lats = [_f(r, "latency_ms", "execution_latency_ms") for r in rows if _f(r, "latency_ms") > 0]
    eqs = [_f(r, "execution_quality_score") for r in rows if r.get("execution_quality_score") is not None]

    err_n = 0
    deg_n = 0
    for r in rows:
        if str(r.get("health_state") or "") == "error":
            err_n += 1
        if bool(r.get("degraded_mode")):
            deg_n += 1

    return TradeSliceMetrics(
        label=label,
        n=n,
        gross_pnl=gross,
        net_pnl=net_sum,
        fees=fees,
        slippage_usd=slip,
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        expectancy_net=exp,
        max_drawdown=_max_dd(pnls),
        variance_pnl=var_p,
        profit_factor=float(pf),
        sharpe_like=float(sharpe_like),
        avg_hold_seconds=statistics.mean(holds) if holds else 0.0,
        avg_latency_ms=statistics.mean(lats) if lats else 0.0,
        avg_exec_quality=statistics.mean(eqs) if eqs else 0.0,
        failure_or_error_rate=err_n / n,
        degraded_rate=deg_n / n,
    )


def filter_events(
    events: Sequence[Mapping[str, Any]],
    *,
    edge_id: Optional[str] = None,
    avenue_name: Optional[str] = None,
    capital_gate: Optional[CapitalGate] = None,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if edge_id is not None and str(ev.get("edge_id") or "").strip() != edge_id:
            continue
        if avenue_name is not None:
            an = str(ev.get("avenue_name") or "").lower()
            if an != avenue_name.lower():
                continue
        if capital_gate is not None:
            if infer_capital_gate(ev) != capital_gate:
                continue
        out.append(dict(ev))
    return out
