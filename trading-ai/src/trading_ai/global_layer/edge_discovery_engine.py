"""
Canonical edge-discovery scoring from registry + optional runtime trade intelligence (machine-usable).

Writes ``edge_discovery_snapshot.json`` under orchestration governance dir.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.global_layer.bot_registry import load_registry
from trading_ai.global_layer.orchestration_paths import edge_discovery_snapshot_path


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _num(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def _load_trade_intel(runtime_root: Optional[Path]) -> Dict[str, Any]:
    if not runtime_root:
        return {}
    p = Path(runtime_root) / "data" / "control" / "trade_cycle_intelligence.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def score_bot_edge(bot: Dict[str, Any]) -> Dict[str, Any]:
    """Heuristic composite — bounded [0,1]; grounded in registry score fields when present."""
    p = bot
    pts = [
        _num(p.get("profitability_score")),
        _num(p.get("truth_score")),
        _num((p.get("performance") or {}).get("composite", {}).get("trust_score")),
        _num(p.get("reliability_score")),
        _num((p.get("promotion_scorecard") or {}).get("promotion_readiness_score")),
    ]
    valid = [x for x in pts if x == x and x > 0]
    base = sum(valid) / max(len(valid), 1) if valid else 0.35
    repeatability = 1.0 - min(1.0, _num((p.get("promotion_scorecard") or {}).get("false_positive_rate")))
    token_eff = _num(p.get("token_efficiency_score")) or 0.5
    robustness = 1.0 - min(1.0, _num((p.get("promotion_scorecard") or {}).get("max_drawdown_pct")) / 100.0)
    upside_speed = _num(p.get("upside_speed_score")) or base
    total = (base * 0.35 + repeatability * 0.2 + token_eff * 0.15 + robustness * 0.15 + upside_speed * 0.15)
    return {
        "bot_id": p.get("bot_id"),
        "edge_score": round(min(1.0, max(0.0, total)), 6),
        "repeatability_score": round(repeatability, 6),
        "truth_quality_score": round(_num(p.get("truth_score")) or base, 6),
        "token_cost_score": round(token_eff, 6),
        "robustness_score": round(robustness, 6),
        "upside_speed_score": round(upside_speed, 6),
        "live_readiness_proximity": str(p.get("permission_level") or ""),
    }


def build_edge_discovery_snapshot(
    *,
    registry_path: Optional[Path] = None,
    runtime_root: Optional[Path] = None,
) -> Dict[str, Any]:
    reg = load_registry(registry_path)
    bots = list(reg.get("bots") or [])
    ranked: List[Dict[str, Any]] = [score_bot_edge(b) for b in bots]
    ranked.sort(key=lambda x: x.get("edge_score") or 0.0, reverse=True)
    intel = _load_trade_intel(runtime_root)
    watchlist = [r.get("bot_id") for r in ranked[:8] if r.get("edge_score", 0) >= 0.55]
    rapid_validation = [r.get("bot_id") for r in ranked[:5] if 0.35 <= (r.get("edge_score") or 0) < 0.55]
    payload = {
        "truth_version": "edge_discovery_snapshot_v1",
        "generated_at": _iso(),
        "mission_note": "Scores optimize evidence-backed upside velocity — not guaranteed returns.",
        "bots_ranked": ranked,
        "high_upside_edge_watchlist": watchlist,
        "rapid_validation_candidates": rapid_validation,
        "trade_intelligence_truth_version": intel.get("truth_version"),
        "trade_intelligence_trade_count": intel.get("trade_count"),
    }
    p = edge_discovery_snapshot_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload
