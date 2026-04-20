"""
One Gate B Coinbase **production tick** — scan + adaptive evaluation artifact; **does not place orders**.

There is **no** long-running Gate B-only daemon in-repo; operators use cron/systemd to invoke this
repeatedly, or run manually. Honesty: ``orders_placed`` is always False here until a separate
execution wiring explicitly connects candidates to CoinbaseClient with all guards.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.runtime_paths import ezras_runtime_root


def _load_scan_rows(root: Path) -> List[Dict[str, Any]]:
    p = root / "data" / "control" / "gate_b_scan_results.json"
    if not p.is_file():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        rows = raw.get("rows") if isinstance(raw, dict) else []
        return [dict(r) for r in rows if isinstance(r, dict)]
    except (OSError, json.JSONDecodeError):
        return []


def run_gate_b_production_tick(
    runtime_root: Optional[Path] = None,
    *,
    persist_gate_b_adaptive_state: bool = False,
) -> Dict[str, Any]:
    """
    Single tick: Gate B scoped adaptive eval + momentum engine on last scan rows.

    Set ``GATE_B_PRODUCTION_TICK_PERSIST_ADAPTIVE=1`` to persist gate_b operating mode (or pass True).
    """
    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)
    persist = persist_gate_b_adaptive_state or (
        (os.environ.get("GATE_B_PRODUCTION_TICK_PERSIST_ADAPTIVE") or "").strip().lower()
        in ("1", "true", "yes")
    )

    from trading_ai.control.live_adaptive_integration import (
        build_live_operating_snapshot,
        run_live_adaptive_evaluation,
    )
    from trading_ai.shark.coinbase_spot.gate_b_engine import GateBMomentumEngine

    snap = build_live_operating_snapshot(
        evaluation_scope="gate_b",
        production_pnl_only=True,
    )
    adaptive = run_live_adaptive_evaluation(
        snap,
        write_proof=True,
        proof_context={
            "entrypoint": "run_gate_b_production_tick",
            "route": "gate_b_production_tick",
            "venue": "coinbase",
            "gate": "gate_b",
            "trade_intent": "production_tick_scan_only",
            "proof_source": "trading_ai.deployment.gate_b_production_tick:run_gate_b_production_tick",
        },
        persist_adaptive_state=persist,
        adaptive_state_key="gate_b",
    )

    rows = _load_scan_rows(root)
    eng = GateBMomentumEngine()
    engine_out = eng.evaluate_entry_candidates(rows, open_product_ids=[], regime_inputs={})

    out: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(root),
        "tick_ok": True,
        "orders_placed": False,
        "persist_gate_b_adaptive_state": persist,
        "adaptive_evaluation_summary": {
            "allow_new_trades": adaptive.get("allow_new_trades"),
            "emergency_brake_triggered": adaptive.get("emergency_brake_triggered"),
            "mode": adaptive.get("current_operating_mode") or adaptive.get("mode"),
        },
        "engine_evaluation": {
            "candidate_count": len(engine_out.get("candidates") or []),
            "gate_b_disabled": engine_out.get("gate_b_disabled"),
            "regime": engine_out.get("regime"),
        },
        "scan_rows_used": len(rows),
        "honesty": (
            "This tick does not submit Coinbase orders. It proves scan+adaptive+engine wiring only. "
            "Continuous operation = external scheduler invoking this command or a future dedicated runner."
        ),
    }
    tick_path = root / "data" / "control" / "gate_b_last_production_tick.json"
    tick_path.parent.mkdir(parents=True, exist_ok=True)
    tick_path.write_text(json.dumps(out, indent=2, default=str) + "\n", encoding="utf-8")

    try:
        from trading_ai.universal_execution.runtime_truth_material_change import refresh_runtime_truth_after_material_change

        r = refresh_runtime_truth_after_material_change(
            reason="gate_b_production_tick",
            runtime_root=root,
            force=False,
            include_advisory=True,
        )
        out["runtime_truth_refresh_after_tick"] = {
            "refresh_complete_and_trustworthy": r.get("refresh_complete_and_trustworthy"),
            "artifacts_refreshed": r.get("artifacts_refreshed"),
        }
    except Exception as exc:
        out["runtime_truth_refresh_after_tick"] = {"error": str(exc)}

    return out
