"""Alias proof for future avenues — delegates to avenue_auto_attach with user-requested filenames."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from trading_ai.prelive import avenue_auto_attach_proof


def run(*, runtime_root: Path) -> Dict[str, Any]:
    inner = avenue_auto_attach_proof.run(runtime_root=runtime_root)
    payload = {
        **inner,
        "artifact_names": [
            "future_avenue_auto_assignment_proof.json",
            "future_avenue_auto_assignment_proof.txt",
        ],
        "honesty": inner.get("honesty"),
    }
    p1 = runtime_root / "data" / "control" / "future_avenue_auto_assignment_proof.json"
    p2 = runtime_root / "data" / "control" / "future_avenue_auto_assignment_proof.txt"
    p1.parent.mkdir(parents=True, exist_ok=True)
    p1.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    p2.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload
