"""Human + machine-readable evolution outputs under runtime / review paths."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from trading_ai.nte.utils.atomic_json import atomic_write_json

logger = logging.getLogger(__name__)


def evolution_report_paths() -> Tuple[Path, Path]:
    """JSON bundle + machine-readable sidecar."""
    try:
        from trading_ai.review.paths import review_data_dir

        base = review_data_dir() / "evolution"
    except Exception:
        base = Path("data") / "review" / "evolution"
    base.mkdir(parents=True, exist_ok=True)
    return base / "evolution_cycle_latest.json", base / "evolution_cycle_latest.min.json"


def write_evolution_artifacts(bundle: Mapping[str, Any]) -> Dict[str, str]:
    human, machine = evolution_report_paths()
    atomic_write_json(human, dict(bundle))
    slim = {
        "schema": bundle.get("schema"),
        "summary": bundle.get("summary"),
        "generated_at": None,
    }
    steps = bundle.get("steps") or []
    if steps:
        last = steps[-1]
        if isinstance(last, dict):
            slim["generated_at"] = last.get("generated_at")
    atomic_write_json(machine, slim)
    return {"full": str(human), "summary": str(machine)}
