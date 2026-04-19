"""
Kalshi avenue readiness — ``kalshi_parity_status.json`` (independent namespace guidance).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.global_layer.trade_truth import load_federated_trades
from trading_ai.nte.memory.store import MemoryStore


def _is_kalshi_row(t: Dict[str, Any]) -> bool:
    av = str(t.get("avenue") or t.get("avenue_name") or "").lower()
    return av == "kalshi" or av == "b"


def build_kalshi_parity_status(*, nte_store: Optional[MemoryStore] = None) -> Dict[str, Any]:
    ms = nte_store or MemoryStore()
    ms.ensure_defaults()
    trades, meta = load_federated_trades(nte_store=ms)
    kalshi_rows = [t for t in trades if _is_kalshi_row(t)]
    rep = (meta.get("avenue_representation") or {}).get("kalshi") if isinstance(meta.get("avenue_representation"), dict) else {}
    return {
        "schema": "kalshi_parity_status_v1",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "kalshi_runtime_namespace_env": os.environ.get("KALSHI_RUNTIME_ROOT") or os.environ.get("EZRAS_RUNTIME_ROOT"),
        "kalshi_federated_trade_rows": len(kalshi_rows),
        "kalshi_representation": rep,
        "expected_kalshi_in_federation": "kalshi" in (meta.get("expected_avenues") or []),
        "warnings": [w for w in (meta.get("warnings") or []) if "Kalshi" in w or "kalshi" in w],
        "independent_databank_note": "Use TRADE_DATABANK_MEMORY_ROOT or EZRAS_RUNTIME_ROOT/databank per session; "
        "optional KALSHI_RUNTIME_ROOT for operator-local Kalshi-only trees.",
    }


def write_kalshi_parity_status(runtime_root: Path, *, nte_store: Optional[MemoryStore] = None) -> Path:
    runtime_root = runtime_root.resolve()
    payload = build_kalshi_parity_status(nte_store=nte_store)
    d = runtime_root / "kalshi_proof"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "kalshi_parity_status.json"
    p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return p
