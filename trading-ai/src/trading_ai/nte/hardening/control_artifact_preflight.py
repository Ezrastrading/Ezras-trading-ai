"""Optional pre-trade checks that operator control artifacts exist and policy is not empty."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.runtime_paths import ezras_runtime_root


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def missing_control_artifacts_for_live_execution(runtime_root: Optional[Path] = None) -> List[str]:
    """
    Returns a list of human-readable gaps (empty list = minimum artifacts present).

    Does not validate freshness or cross-file consistency — only existence + non-empty policy.
    """
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = root / "data" / "control"
    missing: List[str] = []
    required = (
        "runtime_policy_snapshot.json",
        "validation_product_resolution_report.json",
        "quote_capital_truth.json",
        "deployable_capital_report.json",
        "route_selection_report.json",
    )
    for name in required:
        p = ctrl / name
        if not p.is_file():
            missing.append(f"missing_file:{p}")
            continue
        snap = _read_json(p)
        if not snap:
            missing.append(f"empty_or_invalid_json:{p}")

    rp = _read_json(ctrl / "runtime_policy_snapshot.json")
    if rp:
        canon = rp.get("canonical_policy") if isinstance(rp.get("canonical_policy"), dict) else None
        u = rp.get("universal_crypto_runtime_policy")
        if isinstance(u, dict) and u.get("multi_leg_route_execution_enabled") is True:
            multi_on = True
        else:
            multi_on = False
        if multi_on:
            missing.append("universal_policy_claims_multi_leg_execution_enabled")
        if isinstance(canon, dict):
            valid = canon.get("runtime_allowlist_valid")
            if valid is False:
                missing.append("runtime_allowlist_invalid_in_snapshot")
    return missing


def require_control_artifacts_for_live_execution(runtime_root: Optional[Path] = None) -> None:
    """Raise RuntimeError if minimum control artifacts are absent or policy snapshot is invalid."""
    gaps = missing_control_artifacts_for_live_execution(runtime_root=runtime_root)
    if gaps:
        raise RuntimeError("control_artifact_preflight_failed: " + "; ".join(gaps))


def control_artifact_preflight_enabled() -> bool:
    return (os.environ.get("EZRAS_REQUIRE_CONTROL_ARTIFACTS_FOR_LIVE") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
