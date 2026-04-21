"""Write system_truth_final.json — honest labels, no fake live proof."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from trading_ai.prelive._io import write_control_json


def _scan_repo_for_hype(repo_root: Path) -> List[Dict[str, Any]]:
    """Lightweight grep for risky strings in src (best-effort)."""
    hits: List[Dict[str, Any]] = []
    patterns = [
        (r"\blive\b.*\bready\b", "possible_live_ready_wording"),
        (r"\bactive\b.*\bwithout\b", "possible_active_without_invocation"),
    ]
    src = repo_root / "src" / "trading_ai"
    if not src.is_dir():
        return hits
    for py in list(src.rglob("*.py"))[:400]:
        try:
            text = py.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pat, label in patterns:
            if re.search(pat, text, flags=re.IGNORECASE):
                hits.append({"file": str(py.relative_to(repo_root)), "pattern": label})
                break
    return hits[:50]


def run(*, runtime_root: Path, repo_root: Path | None = None) -> Dict[str, Any]:
    rr = Path(repo_root or Path(__file__).resolve().parents[3])
    scan = _scan_repo_for_hype(rr)
    payload: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "live_execution_truth": "code_paths_exist_runtime_gated",
        "validation_truth": "mock_and_staged_harnesses_pass_in_ci_when_run",
        "operator_dashboard_truth": "artifacts_are_advisory_until_operator_confirms",
        "scan_hits_sample": scan,
        "honesty": "This file is machine-generated; it does not certify profitability or venue guarantees.",
    }
    write_control_json("system_truth_final.json", payload, runtime_root=runtime_root)
    return payload
