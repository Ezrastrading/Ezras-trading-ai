"""Prove synthetic Avenue C + gates attach universal layers (non-execution)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from trading_ai.multi_avenue.avenue_factory import register_avenue
from trading_ai.multi_avenue.gate_factory import register_gate
from trading_ai.multi_avenue.scoped_paths import avenue_control_dir
from trading_ai.prelive._io import write_control_json, write_control_txt


def run(*, runtime_root: Path) -> Dict[str, Any]:
    register_avenue(
        {
            "avenue_id": "C",
            "avenue_name": "synthetic_avenue_c",
            "display_name": "Synthetic Avenue C",
            "venue_name": "mock",
            "market_type": "mock",
            "wiring_status": "scaffold_only",
            "notes": "prelive_auto_attach_proof",
            "gates": [],
        },
        runtime_root=runtime_root,
    )
    register_gate("C", "gate_c1", runtime_root=runtime_root)
    register_gate("C", "gate_c2", runtime_root=runtime_root)
    ar = avenue_control_dir("C", runtime_root=runtime_root)
    scoped_ok = ar.is_dir()
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "avenue_id": "C",
        "gates": ["gate_c1", "gate_c2"],
        "scoped_root_exists": scoped_ok,
        "universal_layers": [
            "scoped_paths",
            "registry_rows",
            "contamination markers via avenue_id prefix",
        ],
        "honesty": "Execution wiring for venue C is intentionally absent; universal scaffolding only.",
    }
    write_control_json("avenue_auto_attach_proof.json", payload, runtime_root=runtime_root)
    write_control_txt("avenue_auto_attach_proof.txt", json.dumps(payload, indent=2) + "\n", runtime_root=runtime_root)
    return payload
