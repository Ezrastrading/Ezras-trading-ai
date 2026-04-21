"""Open Gate B position monitor — profit zone, trail, hard stop, max hold."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class GateBMonitorState:
    product_id: str
    entry_price: float
    peak_price: float
    entry_ts: float
    last_price: float

    def observe_price(self, price: float, now_ts: float) -> None:
        self.last_price = float(price)
        if price > self.peak_price:
            self.peak_price = float(price)


def _gain_from_entry(entry: float, last: float) -> float:
    if entry <= 0:
        return 0.0
    return (last - entry) / entry


def _drawdown_from_peak(peak: float, last: float) -> float:
    if peak <= 0:
        return 0.0
    return (peak - last) / peak


def gate_b_monitor_tick(
    st: GateBMonitorState,
    *,
    now_ts: float,
    profit_target_pct: float = 1.0,
    trailing_stop_from_peak_pct: float = 0.03,
    hard_stop_from_entry_pct: float = 0.12,
    max_hold_sec: float = 86_400.0,
    profit_zone_min_pct: Optional[float] = None,
    profit_zone_max_pct: Optional[float] = None,
) -> Dict[str, Any]:
    last = float(st.last_price)
    entry = float(st.entry_price)
    peak = float(st.peak_price)
    gain = _gain_from_entry(entry, last)
    if profit_zone_min_pct is not None and profit_zone_max_pct is not None:
        if gain >= float(profit_zone_max_pct):
            return {"exit": True, "exit_reason": "profit_zone_ceiling"}
        if gain <= float(profit_zone_min_pct):
            return {"exit": False, "exit_reason": "inside_profit_zone"}
    if profit_zone_max_pct is not None and profit_zone_min_pct is None:
        if gain >= float(profit_zone_max_pct):
            return {"exit": True, "exit_reason": "profit_zone_ceiling"}
    if gain >= float(profit_target_pct):
        if profit_zone_max_pct is None or (profit_zone_min_pct is None and profit_zone_max_pct is None):
            return {"exit": True, "exit_reason": "profit_target"}
    dd_peak = _drawdown_from_peak(peak, last)
    if peak > entry and dd_peak >= float(trailing_stop_from_peak_pct):
        return {"exit": True, "exit_reason": "trailing_stop_from_peak"}
    if gain <= -abs(float(hard_stop_from_entry_pct)):
        return {"exit": True, "exit_reason": "hard_stop"}
    if now_ts - float(st.entry_ts) >= float(max_hold_sec):
        return {"exit": True, "exit_reason": "max_hold"}
    return {"exit": False, "exit_reason": "hold"}
