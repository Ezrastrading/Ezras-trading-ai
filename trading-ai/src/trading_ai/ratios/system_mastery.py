"""Grounded self-knowledge artifact — refreshable; not a hallucinated memory source."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from trading_ai.ratios.recent_work_activation import build_recent_work_activation_audit
from trading_ai.ratios.universal_ratio_registry import build_universal_ratio_policy_bundle


def build_last_48h_system_mastery(runtime_root: Path) -> Dict[str, Any]:
    """Summarize what the repo believes about itself — honest about live vs scaffold."""
    rw = build_recent_work_activation_audit(runtime_root=runtime_root)
    bundle = build_universal_ratio_policy_bundle()
    return {
        "artifact": "last_48h_system_mastery",
        "version": "v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope_note": "Covers recent capital/routing/ratio work; not a git log.",
        "authoritative": {
            "nte_products_defaults": "BTC/ETH/SOL x USD/USDC via load_nte_settings",
            "validation_single_leg": "resolve_validation_product_coherent coherent_v6",
            "deployable_capital_artifacts": "when control writes enabled",
            "ratio_registry": "trading_ai.ratios.universal_ratio_registry",
        },
        "partially_wired": {
            "adaptive_multipliers_in_ratio_bundle": "placeholder until adaptive OS exports multipliers here",
            "route_cost_to_edge_ratio": "scaffold_only in universal registry",
        },
        "scaffold_only": [
            "universal.route_cost_to_edge_min_ratio (notes: scaffold_not_live)",
        ],
        "proven_by_tests": [
            "tests/test_universal_capital_routing.py",
            "tests/test_runtime_coinbase_policy_unified.py",
            "tests/test_universal_ratio_layer.py",
        ],
        "runtime_artifact_proven_when": "EZRAS_WRITE_VALIDATION_CONTROL_ARTIFACTS or micro-validation with credentials",
        "not_live": [
            "multi_leg_exchange_execution",
            "automated_daily_ratio_llm_review",
        ],
        "gate_a_consumes": "gate_ratio_access.gate_a_ratio_view + NTE settings",
        "gate_b_consumes": "gate_ratio_access.gate_b_ratio_view + momentum caps",
        "recent_work_activation": rw.get("items"),
        "ratio_policy_version": bundle.ratio_policy_version,
    }


def write_last_48h_system_mastery(runtime_root: Path) -> Dict[str, str]:
    learn = runtime_root / "data" / "learning"
    learn.mkdir(parents=True, exist_ok=True)
    payload = build_last_48h_system_mastery(runtime_root)
    jp = learn / "last_48h_system_mastery.json"
    tp = learn / "last_48h_system_mastery.txt"
    js = json.dumps(payload, indent=2, default=str)
    jp.write_text(js, encoding="utf-8")
    tp.write_text(js[:24000] + "\n", encoding="utf-8")
    # Optional memory file
    mem = learn / "ratio_memory.json"
    mem.write_text(
        json.dumps(
            {
                "ratio_policy_version": payload.get("ratio_policy_version"),
                "last_snapshot_ref": str(runtime_root / "data" / "control" / "ratio_policy_snapshot.json"),
                "updated_at": payload.get("generated_at"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "last_48h_system_mastery_json": str(jp),
        "last_48h_system_mastery_txt": str(tp),
        "ratio_memory_json": str(mem),
    }
