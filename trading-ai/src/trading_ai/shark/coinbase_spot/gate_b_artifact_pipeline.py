"""Write Gate B operational JSON artifacts under data/control (staged, no venue)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict

from trading_ai.shark.coinbase_spot.gate_b_config import load_gate_b_config_from_env
from trading_ai.shark.coinbase_spot.gate_b_engine import GateBMomentumEngine


def write_gate_b_operational_artifacts(*, runtime_root: Path) -> Dict[str, Any]:
    root = Path(runtime_root).resolve()
    ctrl = root / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    cfg = load_gate_b_config_from_env()
    eng = GateBMomentumEngine()
    ts = time.time()
    base = {
        "volume_24h_usd": 5_000_000.0,
        "spread_bps": 12.0,
        "book_depth_usd": 80_000.0,
        "move_pct": 0.08,
        "volume_surge_ratio": 1.8,
        "continuation_candles": 3,
        "velocity_score": 0.62,
        "quote_ts": ts,
        "best_bid": 50_000.0,
        "best_ask": 50_015.0,
        "closes": [48_000 + 80 * i for i in range(24)],
        "exhaustion_risk": 0.2,
    }
    rows = [{**base, "product_id": "BTC-USD"}, {**base, "product_id": "ETH-USD", "move_pct": 0.02}]
    ent = eng.evaluate_entry_candidates(rows, open_product_ids=[], regime_inputs={})
    bundle = {
        "generated_at": ts,
        "strategy_family": cfg.strategy_family,
        "scan_rows": rows,
        "entry": ent,
    }
    (ctrl / "gate_b_scan_results.json").write_text(json.dumps(bundle, indent=2, default=str) + "\n", encoding="utf-8")
    (ctrl / "gate_b_ranked_candidates.json").write_text(
        json.dumps({"candidates": ent.get("candidates", [])}, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (ctrl / "gate_b_selection_decisions.json").write_text(
        json.dumps({"selected": ent.get("candidates", [])[:1]}, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (ctrl / "gate_b_risk_snapshot.json").write_text(
        json.dumps({"edge": ent.get("edge"), "regime": ent.get("regime")}, indent=2, default=str) + "\n", encoding="utf-8"
    )
    return {"ok": True, "paths": [str(ctrl / "gate_b_scan_results.json")]}
