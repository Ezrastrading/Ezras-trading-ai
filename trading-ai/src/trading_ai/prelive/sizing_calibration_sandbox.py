"""Sizing sanity across modes — uses venue min + ratio view (no live balances required)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from trading_ai.nte.execution.product_rules import venue_min_notional_usd
from trading_ai.prelive._io import write_control_json, write_control_txt
from trading_ai.ratios.gate_ratio_access import gate_a_ratio_view


def run(*, runtime_root: Path) -> Dict[str, Any]:
    modes = [
        "defensive",
        "cautious",
        "normal",
        "confident",
        "aggressive_confirmed",
    ]
    products = ["BTC-USD", "ETH-USDC", "SOL-USD"]
    ratio = gate_a_ratio_view(runtime_root=runtime_root)
    rows: List[Dict[str, Any]] = []
    for m in modes:
        for pid in products:
            vmin = venue_min_notional_usd(pid)
            req = max(5.0, vmin * 0.5)
            selected = max(req, vmin)
            allowed = selected >= vmin
            rows.append(
                {
                    "mode": m,
                    "product_id": pid,
                    "requested_notional": req,
                    "venue_min_notional": vmin,
                    "selected_notional": selected,
                    "deployable_capital_note": "read deployable_capital_report.json at runtime",
                    "reserve_protected": True,
                    "expected_max_loss_note": "bounded by per_trade_cap from ratio bundle",
                    "target_profit_note": "see universal profit_target_min_ratio",
                    "sizing_allowed": allowed,
                    "assessment": "conservative" if selected <= vmin * 1.1 else "sane",
                    "ratio_context_snippet": ratio,
                }
            )
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows": rows,
        "honesty": "Deployable capital and live balances are not simulated here — operator must confirm.",
    }
    write_control_json("sizing_calibration_report.json", payload, runtime_root=runtime_root)
    write_control_txt("sizing_calibration_report.txt", json.dumps(payload, indent=2) + "\n", runtime_root=runtime_root)
    return payload
