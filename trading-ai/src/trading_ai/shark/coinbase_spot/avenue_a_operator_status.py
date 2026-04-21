"""Single JSON snapshot for Avenue A / Gate truth + calibration labels."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from trading_ai.shark.coinbase_spot.gate_b_config import GateBConfig
from trading_ai.shark.coinbase_spot.gate_b_tuning_resolver import resolve_gate_b_tuning_artifact


def build_avenue_a_operator_status(*, runtime_root: Path) -> Dict[str, Any]:
    root = Path(runtime_root).resolve()
    tuning = resolve_gate_b_tuning_artifact(deployable_quote_usd=None, measured_slippage_bps=None, baseline_config=GateBConfig())
    art_path = root / "data" / "control" / "gate_a_universe.json"
    gate_a_complete = False
    if art_path.is_file():
        try:
            art = json.loads(art_path.read_text(encoding="utf-8"))
            gate_a_complete = bool(art.get("production_truth_complete"))
        except Exception:
            gate_a_complete = False
    return {
        "runtime_root": str(root),
        "gate_a_production_truth_complete": gate_a_complete,
        "gate_b_calibration_level": tuning.get("calibration_level"),
        "gate_b_tuning_truth_version": tuning.get("truth_version"),
    }


def write_avenue_a_operator_status_artifact(*, runtime_root: Path, payload: Dict[str, Any]) -> Path:
    root = Path(runtime_root).resolve()
    ctrl = root / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    p = ctrl / "avenue_a_operator_status.json"
    p.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return p
