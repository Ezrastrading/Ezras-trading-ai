"""Execution quality vs fees — per-trade slippage, PnL, and fee drag (measurement only)."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from trading_ai.reality.paths import reality_data_dir

logger = logging.getLogger(__name__)


def _bps(actual: float, expected: float) -> float:
    e = abs(float(expected))
    if e < 1e-12:
        return 0.0
    return 1e4 * (float(actual) - float(expected)) / e


def _slippage_usd_from_prices(
    *,
    expected_entry_price: float,
    actual_entry_price: float,
    expected_exit_price: float,
    actual_exit_price: float,
    base_size: float,
) -> float:
    b = abs(float(base_size))
    return (
        abs(float(actual_entry_price) - float(expected_entry_price)) * b
        + abs(float(actual_exit_price) - float(expected_exit_price)) * b
    )


@dataclass
class ExecutionTruthRecord:
    expected_entry_price: float
    actual_entry_price: float
    expected_exit_price: float
    actual_exit_price: float
    base_size: float
    fees_paid: float
    slippage_usd: float
    slippage_bps: float
    slippage_entry_bps: float
    slippage_exit_bps: float
    gross_pnl: float
    net_pnl: float
    execution_drag: float
    execution_drag_ratio: float
    flag: str
    trade_id: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _execution_cost_ratio(*, fees_paid: float, slippage_usd: float, gross_pnl: float) -> float:
    denom = max(abs(float(gross_pnl)), 1e-9)
    return (float(fees_paid) + float(slippage_usd)) / denom


def compute_execution_truth(
    *,
    expected_entry_price: float,
    actual_entry_price: float,
    expected_exit_price: float,
    actual_exit_price: float,
    base_size: float,
    fees_paid: float,
    trade_id: Optional[str] = None,
) -> ExecutionTruthRecord:
    b = float(base_size)
    gross_pnl = (float(actual_exit_price) - float(actual_entry_price)) * b
    net_pnl = gross_pnl - float(fees_paid)
    slip_usd = _slippage_usd_from_prices(
        expected_entry_price=expected_entry_price,
        actual_entry_price=actual_entry_price,
        expected_exit_price=expected_exit_price,
        actual_exit_price=actual_exit_price,
        base_size=b,
    )
    se = _bps(actual_entry_price, expected_entry_price)
    sx = _bps(actual_exit_price, expected_exit_price)
    slip_bps = (abs(se) + abs(sx)) / 2.0
    execution_drag = float(fees_paid) + slip_usd
    execution_drag_ratio = _execution_cost_ratio(
        fees_paid=float(fees_paid), slippage_usd=slip_usd, gross_pnl=gross_pnl
    )
    flag = "EXECUTION_KILLING_EDGE" if execution_drag_ratio > 0.5 else "OK"
    return ExecutionTruthRecord(
        expected_entry_price=float(expected_entry_price),
        actual_entry_price=float(actual_entry_price),
        expected_exit_price=float(expected_exit_price),
        actual_exit_price=float(actual_exit_price),
        base_size=b,
        fees_paid=float(fees_paid),
        slippage_usd=slip_usd,
        slippage_bps=slip_bps,
        slippage_entry_bps=se,
        slippage_exit_bps=sx,
        gross_pnl=gross_pnl,
        net_pnl=net_pnl,
        execution_drag=execution_drag,
        execution_drag_ratio=execution_drag_ratio,
        flag=flag,
        trade_id=trade_id,
    )


def _slippage_usd_from_merged(merged: Mapping[str, Any], *, base_size: float) -> float:
    explicit = float(merged.get("total_slippage_usd") or merged.get("slippage_usd") or 0.0)
    if explicit > 0.0:
        return explicit
    ie = float(merged.get("intended_entry_price") or 0.0)
    ia = float(merged.get("actual_entry_price") or 0.0)
    ix = float(merged.get("intended_exit_price") or 0.0)
    ox = float(merged.get("actual_exit_price") or 0.0)
    if ie > 1e-12 and ix > 1e-12:
        return _slippage_usd_from_prices(
            expected_entry_price=ie,
            actual_entry_price=ia,
            expected_exit_price=ix,
            actual_exit_price=ox,
            base_size=base_size,
        )
    e_slip = float(merged.get("entry_slippage_bps") or 0.0)
    x_slip = float(merged.get("exit_slippage_bps") or 0.0)
    mid_notional = abs(ia * base_size) if ia > 1e-12 else max(1.0, abs(float(merged.get("gross_pnl") or 0.0)))
    return (abs(e_slip) + abs(x_slip)) / 2.0 / 1e4 * mid_notional


def compute_execution_truth_from_merged_trade(merged: Mapping[str, Any]) -> Optional[ExecutionTruthRecord]:
    """
    Build :class:`ExecutionTruthRecord` from a Trade Intelligence databank row (post-close).
    Returns None when prices are unusable (cannot attribute execution cost safely).
    """
    tid = str(merged.get("trade_id") or "").strip() or None
    ie = float(merged.get("intended_entry_price") or 0.0)
    ia = float(merged.get("actual_entry_price") or 0.0)
    ix = float(merged.get("intended_exit_price") or 0.0)
    ox = float(merged.get("actual_exit_price") or 0.0)
    b_raw = merged.get("base_qty")
    if b_raw is None:
        b_raw = merged.get("contracts")
    b = float(b_raw) if b_raw is not None else 1.0
    fees = float(merged.get("fees_paid") or merged.get("fees_usd") or merged.get("fees") or 0.0)

    if ie > 1e-12 and ix > 1e-12 and ia > 1e-12 and ox > 1e-12:
        return compute_execution_truth(
            expected_entry_price=ie,
            actual_entry_price=ia,
            expected_exit_price=ix,
            actual_exit_price=ox,
            base_size=b,
            fees_paid=fees,
            trade_id=tid,
        )

    gross = float(merged.get("gross_pnl") or 0.0)
    net = float(merged.get("net_pnl") or merged.get("net_pnl_usd") or 0.0)
    if abs(gross) < 1e-12 and abs(net) < 1e-12 and fees < 1e-12:
        return None

    slip_usd = _slippage_usd_from_merged(merged, base_size=max(b, 1e-12))
    e_slip = float(merged.get("entry_slippage_bps") or 0.0)
    x_slip = float(merged.get("exit_slippage_bps") or 0.0)
    slip_bps = (abs(e_slip) + abs(x_slip)) / 2.0
    execution_drag = fees + slip_usd
    execution_drag_ratio = _execution_cost_ratio(fees_paid=fees, slippage_usd=slip_usd, gross_pnl=gross)
    flag = "EXECUTION_KILLING_EDGE" if execution_drag_ratio > 0.5 else "OK"
    return ExecutionTruthRecord(
        expected_entry_price=ie,
        actual_entry_price=ia,
        expected_exit_price=ix,
        actual_exit_price=ox,
        base_size=b,
        fees_paid=fees,
        slippage_usd=slip_usd,
        slippage_bps=slip_bps,
        slippage_entry_bps=e_slip,
        slippage_exit_bps=x_slip,
        gross_pnl=gross,
        net_pnl=net,
        execution_drag=execution_drag,
        execution_drag_ratio=execution_drag_ratio,
        flag=flag,
        trade_id=tid,
    )


def execution_truth_path(path: Optional[Path] = None) -> Path:
    return (path or reality_data_dir()) / "execution_truth.jsonl"


def append_execution_truth_record(
    record: ExecutionTruthRecord,
    *,
    path: Optional[Path] = None,
    emit_kill_alert: bool = True,
) -> None:
    p = execution_truth_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record.to_dict(), default=str) + "\n")
    if emit_kill_alert and record.flag == "EXECUTION_KILLING_EDGE":
        try:
            from trading_ai.control.alerts import emit_alert

            emit_alert("WARNING", "Execution degrading edge")
        except Exception as exc:
            logger.debug("execution truth alert skipped: %s", exc)


def append_execution_truth_from_databank_trade(merged: Mapping[str, Any]) -> Optional[ExecutionTruthRecord]:
    """Append one execution_truth.jsonl line from a validated databank trade row."""
    ex = compute_execution_truth_from_merged_trade(merged)
    if ex is None:
        return None
    append_execution_truth_record(ex)
    return ex
