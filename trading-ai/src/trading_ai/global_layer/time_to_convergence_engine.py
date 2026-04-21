"""
Time-to-convergence scoring — ranks paths by estimated validation / implementation / usefulness velocity.

Writes ``time_to_convergence_snapshot.json`` under orchestration governance dir.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.global_layer.bot_registry import load_registry
from trading_ai.global_layer.orchestration_paths import time_to_convergence_snapshot_path


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _num(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def score_time_to_usefulness(bot: Dict[str, Any]) -> Dict[str, Any]:
    ps = _num(bot.get("promotion_velocity_score"))
    cs = _num(bot.get("convergence_score"))
    impl = _num(bot.get("implementation_speed_score"))
    rs = _num((bot.get("promotion_scorecard") or {}).get("promotion_readiness_score"))
    scale = _num(bot.get("scale_score"))
    validation_speed = max(rs, cs, 0.1)
    implementation_speed = max(impl, ps * 0.5, 0.1)
    profitability_convergence = max(cs, _num(bot.get("profitability_score")), 0.1)
    promotion_readiness = rs
    total = (
        validation_speed * 0.25
        + implementation_speed * 0.2
        + profitability_convergence * 0.25
        + promotion_readiness * 0.15
        + max(scale, 0.1) * 0.15
    )
    return {
        "bot_id": bot.get("bot_id"),
        "validation_speed_score": round(min(1.0, validation_speed), 6),
        "implementation_speed_score": round(min(1.0, implementation_speed), 6),
        "profitability_convergence_score": round(min(1.0, profitability_convergence), 6),
        "promotion_readiness_score": round(min(1.0, promotion_readiness), 6),
        "scale_potential_score": round(min(1.0, scale), 6),
        "total_time_to_usefulness_score": round(min(1.0, total), 6),
    }


def build_time_to_convergence_snapshot(*, registry_path: Optional[Path] = None) -> Dict[str, Any]:
    reg = load_registry(registry_path)
    bots = list(reg.get("bots") or [])
    rows = [score_time_to_usefulness(b) for b in bots]
    rows.sort(key=lambda x: x.get("total_time_to_usefulness_score") or 0.0, reverse=True)
    payload = {
        "truth_version": "time_to_convergence_snapshot_v1",
        "generated_at": _iso(),
        "mission_note": "Higher scores imply faster route to measured usefulness — not guaranteed calendar time.",
        "paths_ranked": rows,
    }
    p = time_to_convergence_snapshot_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload
