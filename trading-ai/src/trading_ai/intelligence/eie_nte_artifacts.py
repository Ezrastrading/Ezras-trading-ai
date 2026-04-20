"""NTE memory artifacts for execution intelligence + truth summaries."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.nte.paths import nte_memory_dir


def _write(name: str, payload: Dict[str, Any]) -> Path:
    p = nte_memory_dir() / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return p


def write_execution_intelligence_nte_bundle(
    bundle: Dict[str, Any],
    *,
    discrepancy_report: Optional[Dict[str, Any]] = None,
    truth_source_summary: Optional[Dict[str, Any]] = None,
    runtime_root: Optional[str] = None,
) -> Dict[str, Path]:
    """Persist machine-readable EIE outputs next to NTE memory."""
    ts = datetime.now(timezone.utc).isoformat()
    base = {
        "truth_version": "execution_intelligence_nte_bundle_v1",
        "generated_at": ts,
        "runtime_root": runtime_root,
        "source_policy_used": (bundle.get("system_state") or {}).get("source_policy_used"),
        "honesty": "Advisory snapshot — does not place orders or override governance.",
    }
    paths: Dict[str, Path] = {}
    paths["execution_intelligence_snapshot.json"] = _write(
        "execution_intelligence_snapshot.json",
        {**base, "bundle": {k: v for k, v in bundle.items() if k != "_raw_trades"}},
    )
    if discrepancy_report:
        paths["execution_intelligence_discrepancy_report.json"] = _write(
            "execution_intelligence_discrepancy_report.json",
            {
                "truth_version": "ei_discrepancy_v1",
                "generated_at": ts,
                "runtime_root": runtime_root,
                **discrepancy_report,
            },
        )
    if truth_source_summary:
        paths["truth_source_summary.json"] = _write(
            "truth_source_summary.json",
            {
                "truth_version": "truth_source_summary_v1",
                "generated_at": ts,
                "runtime_root": runtime_root,
                **truth_source_summary,
            },
        )
    return paths
