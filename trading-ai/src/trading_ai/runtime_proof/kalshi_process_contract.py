"""
Kalshi separate-process contract — isolated roots and readiness artifacts.

``KALSHI_RUNTIME_ROOT`` (optional): dedicated tree for Kalshi-only processes so trade ids,
databank rows, and scheduler state do not share a Coinbase/NTE working directory.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.nte.memory.store import MemoryStore
from trading_ai.runtime_proof.kalshi_readiness_report import build_kalshi_parity_status


def resolve_kalshi_runtime_root(explicit: Optional[str] = None) -> Optional[Path]:
    raw = explicit if explicit is not None else os.environ.get("KALSHI_RUNTIME_ROOT")
    if not (raw or "").strip():
        return None
    return Path(raw).expanduser().resolve()


def build_kalshi_process_readiness(runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    kr = resolve_kalshi_runtime_root()
    ez = (os.environ.get("EZRAS_RUNTIME_ROOT") or "").strip()
    db_general = (os.environ.get("TRADE_DATABANK_MEMORY_ROOT") or "").strip()
    session_ref = runtime_root or (Path(ez) if ez else Path("."))

    isolated_db = None
    isolated_artifacts = None
    isolated_goals = None
    if kr is not None:
        isolated_db = str(kr / "databank")
        isolated_artifacts = str(kr / "kalshi_proof")
        isolated_goals = str(kr / "goal_proof")

    return {
        "schema": "kalshi_process_readiness_v1",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "kalshi_runtime_root": str(kr) if kr else None,
        "ezras_runtime_root": ez or None,
        "trade_databank_memory_root": db_general or None,
        "session_runtime_reference": str(session_ref.resolve()),
        "isolated_databank_under_kalshi_root": isolated_db,
        "isolated_artifacts_under_kalshi_root": isolated_artifacts,
        "isolated_avenue_goal_tracking": isolated_goals,
        "contract": {
            "set_kalshi_runtime_root": "export KALSHI_RUNTIME_ROOT=/path/to/kalshi_only_tree",
            "recommended_databank": "export TRADE_DATABANK_MEMORY_ROOT=$KALSHI_RUNTIME_ROOT/databank",
            "trade_id_prefix_note": "Kalshi execution paths should use venue-scoped ids; do not reuse Coinbase client_order ids.",
        },
        "contamination_checks": {
            "same_root_as_coinbase_session": bool(kr and ez and str(kr) == str(Path(ez).resolve())),
            "warning_if_shared_root": bool(kr and ez and str(kr) == str(Path(ez).resolve())),
        },
    }


def build_kalshi_isolation_report(
    *,
    runtime_root: Path,
    nte_store: Optional[MemoryStore] = None,
) -> Dict[str, Any]:
    """Parity / isolation snapshot for operator review (no live trading required)."""
    parity = build_kalshi_parity_status(nte_store=nte_store)
    kr = resolve_kalshi_runtime_root()
    ez = Path(os.environ.get("EZRAS_RUNTIME_ROOT") or runtime_root).resolve()
    warnings: List[str] = []
    if kr is not None and str(kr.resolve()) == str(ez.resolve()):
        warnings.append(
            "KALSHI_RUNTIME_ROOT equals EZRAS_RUNTIME_ROOT — process isolation is not separated on disk."
        )
    fed_k = int(parity.get("kalshi_federated_trade_rows") or 0)
    if fed_k == 0 and parity.get("expected_kalshi_in_federation"):
        warnings.append("Kalshi expected in federation but zero Kalshi rows — ingest may be incomplete.")

    return {
        "schema": "kalshi_isolation_report_v1",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runtime_root": str(runtime_root.resolve()),
        "kalshi_parity_embedded": parity,
        "warnings": warnings,
        "isolation_ok": len(warnings) == 0,
    }


def write_kalshi_process_artifacts(runtime_root: Path, *, nte_store: Optional[MemoryStore] = None) -> Dict[str, Path]:
    runtime_root = runtime_root.resolve()
    d = runtime_root / "kalshi_proof"
    d.mkdir(parents=True, exist_ok=True)
    p1 = d / "kalshi_process_readiness.json"
    p2 = d / "kalshi_isolation_report.json"
    p1.write_text(json.dumps(build_kalshi_process_readiness(runtime_root), indent=2), encoding="utf-8")
    p2.write_text(json.dumps(build_kalshi_isolation_report(runtime_root=runtime_root, nte_store=nte_store), indent=2), encoding="utf-8")
    return {"readiness": p1, "isolation": p2}
