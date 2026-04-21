"""Human-readable post-trade execution health snapshot under ``data/control``."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from trading_ai.control.paths import control_data_dir

logger = logging.getLogger(__name__)


def reality_snapshot_path() -> Path:
    return control_data_dir() / "reality_snapshot.txt"


def _fmt_pnl(x: float) -> str:
    s = f"{x:+.2f}"
    return s


def _health_line(ok: bool, label: str) -> str:
    return f"- {label}: {'OK' if ok else 'DEGRADED'}"


def update_reality_snapshot_after_trade(
    merged: Mapping[str, Any],
    *,
    events: List[Dict[str, Any]],
    execution_record: Optional[Mapping[str, Any]] = None,
    fill_eval: Optional[Mapping[str, Any]] = None,
) -> None:
    """
    Rewrite ``reality_snapshot.txt`` with last trade, last five net pnls, and health lines.
    """
    p = reality_snapshot_path()
    try:
        net = float(merged.get("net_pnl") or merged.get("net_pnl_usd") or 0.0)
        asset = str(merged.get("asset") or "?")
        last_block = f"LAST TRADE:\n{_fmt_pnl(net)} ({asset})\n"

        tail = events[-5:] if len(events) >= 5 else list(events)
        lines = [_fmt_pnl(float(e.get("net_pnl") or e.get("net_pnl_usd") or 0.0)) for e in tail if isinstance(e, dict)]
        net5 = sum(float(e.get("net_pnl") or e.get("net_pnl_usd") or 0.0) for e in tail if isinstance(e, dict))

        last5_block = "LAST 5 TRADES:\n" + ("\n".join(lines) if lines else "(none)") + "\n"
        net5_block = f"\nNET LAST 5:\n{_fmt_pnl(net5)}\n"

        ex = execution_record or {}
        flag = str(ex.get("flag") or "")
        slip_ok = flag != "EXECUTION_KILLING_EDGE"
        fees_ok = slip_ok
        fq = fill_eval or {}
        fills_ok = not bool(fq.get("poor_fill_quality"))

        degraded = not (slip_ok and fees_ok and fills_ok)
        header_exec = "EXECUTION HEALTH:\n" if not degraded else "EXECUTION HEALTH: DEGRADED\n"
        health = (
            header_exec
            + _health_line(slip_ok, "slippage")
            + "\n"
            + _health_line(fees_ok, "fees")
            + "\n"
            + _health_line(fills_ok, "fills")
            + "\n"
        )
        if degraded:
            health += "\nSTATUS: DEGRADED — review execution_truth + fill quality\n"

        body = last_block + "\n" + last5_block + net5_block + "\n" + health
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    except Exception as exc:
        logger.warning("reality_snapshot update failed: %s", exc)
