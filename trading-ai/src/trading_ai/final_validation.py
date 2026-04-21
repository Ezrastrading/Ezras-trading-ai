"""
Final validation artifact for the whole system.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.storage.storage_adapter import LocalStorageAdapter


def compute_final_system_status(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    root = ad.root()

    exec_ok = (root / "execution_proof" / "execution_proof.json").is_file()
    pnl_ok = (root / "data" / "pnl" / "pnl_record.json").is_file()
    risk_ok = (root / "data" / "risk" / "risk_state.json").is_file()
    truth_chain = ad.read_json("data/control/truth_chain_last.json") or {}
    truth_ok = bool((truth_chain or {}).get("ok")) if isinstance(truth_chain, dict) else False

    # Snapshot chain (strict) — runtime snapshot tables are the canonical fail-closed truth streams.
    master_ok = (root / "data" / "snapshots" / "trades_master.jsonl").is_file()
    edge_ok = (root / "data" / "snapshots" / "trades_edge_snapshot.jsonl").is_file()
    exec_snap_ok = (root / "data" / "snapshots" / "trades_execution_snapshot.jsonl").is_file()
    review_ok = (root / "data" / "snapshots" / "trades_review_snapshot.jsonl").is_file()
    snapshots_ok = bool(master_ok and edge_ok and exec_snap_ok and review_ok)

    # Strict joined reporting must exist and must be computable without missing data.
    metrics_ok = False
    metrics_err: Optional[str] = None
    try:
        from trading_ai.reports.validated_metrics import build_validated_metrics

        _ = build_validated_metrics(runtime_root=root)
        metrics_ok = True
    except Exception as exc:
        metrics_ok = False
        metrics_err = f"{type(exc).__name__}:{exc}"

    # Architecture evidence is in the repo (not runtime). Accept either location.
    repo_guess = Path(__file__).resolve().parents[2]  # trading-ai/src
    scalable_arch = (
        (root / "multi_venue" / "registry.json").is_file()
        or (root.parent / "multi_venue" / "registry.json").is_file()
        or (repo_guess.parent / "multi_venue" / "registry.json").is_file()  # trading-ai/multi_venue/registry.json
    )

    out = {
        "execution_real": bool(exec_ok),
        "truth_valid": bool(truth_ok and snapshots_ok and metrics_ok),
        "risk_enforced": bool(risk_ok),
        "scalable_architecture": bool(scalable_arch),
        "live_ready": bool(exec_ok and pnl_ok and risk_ok and truth_ok and snapshots_ok and metrics_ok),
        "evidence": {
            "execution_proof": "execution_proof/execution_proof.json" if exec_ok else None,
            "pnl_record": "data/pnl/pnl_record.json" if pnl_ok else None,
            "risk_state": "data/risk/risk_state.json" if risk_ok else None,
            "truth_chain_last": "data/control/truth_chain_last.json" if truth_chain else None,
            "snapshots": {
                "master": "data/snapshots/trades_master.jsonl" if master_ok else None,
                "edge": "data/snapshots/trades_edge_snapshot.jsonl" if edge_ok else None,
                "execution": "data/snapshots/trades_execution_snapshot.jsonl" if exec_snap_ok else None,
                "review": "data/snapshots/trades_review_snapshot.jsonl" if review_ok else None,
            },
            "validated_metrics": {
                "path": "data/reports/validated_metrics.json" if metrics_ok else None,
                "error": metrics_err,
            },
        },
    }
    ad.write_json("data/control/FINAL_SYSTEM_STATUS.json", out)
    return out

