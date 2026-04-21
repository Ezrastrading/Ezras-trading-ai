"""Write ratio_policy_snapshot, reserve reports, mirror to data/routing, optional change log."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from trading_ai.ratios.reserve_compute import build_reserve_capital_report
from trading_ai.ratios.universal_ratio_registry import (
    RatioPolicyBundle,
    build_universal_ratio_policy_bundle,
)


def _mirror(
    runtime_root: Path,
    name: str,
    payload: Dict[str, Any],
) -> Tuple[Path, Path, Path, Path]:
    ctrl = runtime_root / "data" / "control"
    rout = runtime_root / "data" / "routing"
    ctrl.mkdir(parents=True, exist_ok=True)
    rout.mkdir(parents=True, exist_ok=True)
    js = json.dumps(payload, indent=2, default=str)
    cj = ctrl / f"{name}.json"
    ct = ctrl / f"{name}.txt"
    rj = rout / f"{name}.json"
    rt = rout / f"{name}.txt"
    for p, content in ((cj, js), (rj, js), (ct, js[:18000] + "\n"), (rt, js[:18000] + "\n")):
        p.write_text(content, encoding="utf-8")
    return cj, ct, rj, rt


def _append_ratio_change_log(runtime_root: Path, line: Dict[str, Any]) -> None:
    p = runtime_root / "data" / "review" / "ratio_change_log.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, default=str) + "\n")


def refresh_ratio_artifacts_after_validation(
    *,
    runtime_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    After deployable/capital control artifacts are written, refresh ratio snapshot + reserve.

    Bounded: no daily review / mastery here (readiness or CLI). Disabled with
    ``EZRAS_RATIO_REFRESH_ON_VALIDATION=0``.
    """
    if (os.environ.get("EZRAS_RATIO_REFRESH_ON_VALIDATION") or "1").strip().lower() in (
        "0",
        "false",
        "no",
    ):
        return {"skipped": True, "reason": "EZRAS_RATIO_REFRESH_ON_VALIDATION disabled"}
    out = write_all_ratio_artifacts(runtime_root=runtime_root, append_change_log=False)
    out["refreshed_after"] = "validation_control_artifacts"
    return out


def write_all_ratio_artifacts(
    *,
    runtime_root: Optional[Path] = None,
    operating_mode: str = "normal",
    append_change_log: bool = True,
) -> Dict[str, str]:
    """
    Writes ratio_policy_snapshot, reserve_capital_report; mirrors under data/routing.

    Expects ``deployable_capital_report.json`` under control when available (from validation).
    """
    from trading_ai.runtime_paths import ezras_runtime_root

    root = Path(runtime_root or ezras_runtime_root()).resolve()
    bundle = build_universal_ratio_policy_bundle(operating_mode=operating_mode)
    ctrl = root / "data" / "control"

    snap = {
        "artifact": "ratio_policy_snapshot",
        "version": "v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "bundle": bundle.to_dict(),
        "labeling_rules": {
            "every_ratio_key_includes_scope": True,
            "no_silent_gate_override": "gate-specific keys are separate from universal.*",
        },
    }
    out: Dict[str, str] = {}
    cj, _ct, _rj, _rt = _mirror(root, "ratio_policy_snapshot", snap)
    out["ratio_policy_snapshot_json"] = str(cj)

    res = build_reserve_capital_report(bundle=bundle, control_dir=ctrl)
    cj2, _, _, _ = _mirror(root, "reserve_capital_report", res)
    out["reserve_capital_report_json"] = str(cj2)

    # Plain-text deployable from JSON if exists (idempotent helper)
    dep = ctrl / "deployable_capital_report.json"
    if dep.is_file():
        try:
            d = json.loads(dep.read_text(encoding="utf-8"))
            (ctrl / "deployable_capital_report.txt").write_text(
                json.dumps(d, indent=2, default=str)[:20000] + "\n",
                encoding="utf-8",
            )
            out["deployable_capital_report_txt_refreshed"] = str(ctrl / "deployable_capital_report.txt")
        except (json.JSONDecodeError, OSError):
            pass

    if append_change_log:
        _append_ratio_change_log(
            root,
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": "ratio_policy_snapshot_written",
                "ratio_policy_version": bundle.ratio_policy_version,
            },
        )
        out["ratio_change_log"] = str(root / "data" / "review" / "ratio_change_log.jsonl")

    return out


def load_ratio_policy_bundle_from_snapshot(runtime_root: Path) -> Optional[RatioPolicyBundle]:
    """If snapshot exists, still rebuild from settings (single source of truth)."""
    p = runtime_root / "data" / "control" / "ratio_policy_snapshot.json"
    if not p.is_file():
        return None
    return build_universal_ratio_policy_bundle()
