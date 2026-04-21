"""
Canonical PnL engine.

All systems (Telegram, Supabase, databank, reviews) must use the same structure:
{
  "gross_pnl": float,
  "fees": float,
  "slippage": float,
  "net_pnl": float
}
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.storage.storage_adapter import LocalStorageAdapter


@dataclass(frozen=True)
class PnlRecord:
    gross_pnl: float
    fees: float
    slippage: float
    net_pnl: float

    def to_dict(self) -> Dict[str, float]:
        return {
            "gross_pnl": float(self.gross_pnl),
            "fees": float(self.fees),
            "slippage": float(self.slippage),
            "net_pnl": float(self.net_pnl),
        }


def compute_round_trip_pnl(
    *,
    buy_quote_spent: float,
    sell_quote_received: float,
    buy_fees: float,
    sell_fees: float,
    entry_slippage_bps: Optional[float] = None,
    exit_slippage_bps: Optional[float] = None,
) -> PnlRecord:
    gross = float(sell_quote_received) - float(buy_quote_spent)
    fees = float(buy_fees) + float(sell_fees)
    slip = 0.0
    try:
        # Slippage is reported as bps, but we store a scalar hint for accounting. We keep it
        # as absolute bps sum (not USD) to avoid double-counting fees/slippage into net_pnl.
        slip = float(entry_slippage_bps or 0.0) + float(exit_slippage_bps or 0.0)
    except Exception:
        slip = 0.0
    net = gross - fees
    return PnlRecord(gross_pnl=gross, fees=fees, slippage=slip, net_pnl=net)


def write_pnl_record(
    record: PnlRecord,
    *,
    runtime_root: Optional[Path] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Writes `data/pnl/pnl_record.json` under runtime root.
    """
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    payload: Dict[str, Any] = {
        **record.to_dict(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        payload["extra"] = dict(extra)
    ad.write_json("data/pnl/pnl_record.json", payload)
    return {"path": "data/pnl/pnl_record.json", "ok": True}

