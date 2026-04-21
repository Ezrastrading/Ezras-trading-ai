"""Per-gate registry — status, scanners, execution hints. Advisory / honest flags only."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.multi_avenue.avenue_registry import merged_avenue_definitions
from trading_ai.runtime_paths import ezras_runtime_root


def _is_asymmetric_gate_id(gate_id: str) -> bool:
    gid = str(gate_id or "").strip().lower()
    return gid.endswith("_asymmetric") or gid.endswith("_asym")


def _generic_gate_row(aid: str, gid: str, avenue: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "avenue_id": aid,
        "gate_id": gid,
        "gate_name": f"Gate {gid} — {avenue.get('display_name', aid)}",
        "production_state_hint": "scaffold_until_wired",
        "status": "scaffold_only",
        "scanner_framework_present": True,
        "active_scanner_modules": [],
        "execution_present": False,
        "review_eligibility": True,
        "artifact_generation_eligibility": True,
        "intentionally_disabled": False,
    }


def merged_gate_rows(*, runtime_root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Flatten avenue → gate with universal framework eligibility (defaults + overlay)."""
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    rows: List[Dict[str, Any]] = []
    for av in merged_avenue_definitions(runtime_root=root):
        aid = str(av["avenue_id"])
        for gid in av.get("gates") or []:
            gid = str(gid)
            if _is_asymmetric_gate_id(gid):
                rows.append(
                    {
                        "avenue_id": aid,
                        "gate_id": gid,
                        "gate_name": f"Asymmetric Gate — {av.get('display_name', aid)}",
                        "production_state_hint": "isolated_capital_bucket_planning_only_until_wired",
                        "status": "planning_shell",
                        "scanner_framework_present": True,
                        "active_scanner_modules": [
                            {
                                "scanner_id": "asymmetric_gate_engine",
                                "module": "trading_ai.asymmetric.asymmetric_gate_engine",
                                "entrypoint": "asymmetric_gate_cycle",
                                "honest_note": "Provides capital isolation + batch sizing + tracker scaffolding; venue-specific scanning/execution must be wired separately.",
                            }
                        ],
                        "execution_present": False,
                        "review_eligibility": True,
                        "artifact_generation_eligibility": True,
                        "intentionally_disabled": False,
                        "gate_type": "asymmetric",
                        "trade_type": "asymmetric",
                        "capital_bucket_id": f"asymmetric:{aid}",
                    }
                )
                continue
            if aid == "A" and gid == "gate_a":
                rows.append(
                    {
                        "avenue_id": aid,
                        "gate_id": gid,
                        "gate_name": "Gate A — NTE / Coinbase single-leg",
                        "production_state_hint": "see_gate_a_live_truth_and_validation",
                        "status": "validation_ready",
                        "scanner_framework_present": True,
                        "active_scanner_modules": [
                            {
                                "scanner_id": "nte_coinbase_monitoring",
                                "module_hint": "trading_ai.nte.execution.coinbase_engine",
                                "honest_note": "Execution engine — not a separate scanner binary.",
                            }
                        ],
                        "execution_present": True,
                        "review_eligibility": True,
                        "artifact_generation_eligibility": True,
                        "intentionally_disabled": False,
                    }
                )
            elif aid == "B" and gid == "gate_b":
                rows.append(
                    {
                        "avenue_id": aid,
                        "gate_id": gid,
                        "gate_name": "Gate B — Kalshi / momentum",
                        "production_state_hint": "STATE_A/B/C via gate_b_live_status_report",
                        "status": "logic_present",
                        "scanner_framework_present": True,
                        "active_scanner_modules": [
                            {
                                "scanner_id": "kalshi_simple_scanner",
                                "module": "trading_ai.shark.kalshi_simple_scanner",
                                "honest_note": "Present in-repo; enablement depends on scheduler / env.",
                            }
                        ],
                        "execution_present": True,
                        "review_eligibility": True,
                        "artifact_generation_eligibility": True,
                        "intentionally_disabled": False,
                    }
                )
            else:
                rows.append(_generic_gate_row(aid, gid, av))
    return rows


def default_gate_rows() -> List[Dict[str, Any]]:
    """Backward-compatible alias — uses merged registry at canonical runtime root."""
    return merged_gate_rows()


def build_gate_registry_snapshot(*, runtime_root: Path | None = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    return {
        "artifact": "gate_registry_snapshot",
        "version": "v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(root),
        "gates": merged_gate_rows(runtime_root=root),
        "future_gate_template": {
            "scanner_framework_ready": True,
            "no_active_scanner_modules_yet": True,
            "review_framework_ready": True,
            "research_framework_ready": True,
            "execution_not_present_until_wired": True,
        },
    }
