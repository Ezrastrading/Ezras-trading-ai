"""Single canonical operator snapshot artifact (file-backed)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.global_layer.bot_registry import load_registry
from trading_ai.global_layer.orchestration_kill_switch import load_kill_switch
from trading_ai.global_layer.orchestration_paths import operator_snapshot_path
from trading_ai.global_layer.capital_governor import load_capital_registry


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _classify(bot: Dict[str, Any]) -> List[str]:
    tags: List[str] = []
    st = str(bot.get("lifecycle_state") or "")
    if st in ("shadow", "initialized", "proposed"):
        tags.append("shadow_lane")
    if st == "eligible":
        tags.append("promotion_eligible")
    if bot.get("promotion_eligibility") is True:
        tags.append("tier_eligible_flag")
    if st in ("frozen", "paused"):
        tags.append("stalled")
    hb = str(bot.get("last_heartbeat_at") or "")
    if not hb:
        tags.append("no_heartbeat")
    if float(bot.get("token_budget_remaining") or 0) <= 0:
        tags.append("budget_exhausted")
    return tags


def build_operator_snapshot(*, registry_path: Optional[Path] = None) -> Dict[str, Any]:
    reg = load_registry(registry_path)
    ks = load_kill_switch()
    cap = load_capital_registry()
    bots = list(reg.get("bots") or [])
    best = None
    worst = None
    best_u = -1.0
    worst_u = 1.0
    rows = []
    for b in bots:
        perf = b.get("performance") or {}
        comp = perf.get("composite") if isinstance(perf, dict) else {}
        u = float((comp or {}).get("utility_score") or 0.0)
        q = b.get("quality_contract") if isinstance(b.get("quality_contract"), dict) else {}
        qu = float(q.get("composite_quality") or u)
        if qu > best_u:
            best_u = qu
            best = b.get("bot_id")
        if qu < worst_u:
            worst_u = qu
            worst = b.get("bot_id")
        rows.append(
            {
                "bot_id": b.get("bot_id"),
                "avenue": b.get("avenue"),
                "gate": b.get("gate"),
                "lifecycle_state": b.get("lifecycle_state"),
                "execution_rung": b.get("execution_rung"),
                "promotion_tier": b.get("promotion_tier"),
                "capital_authority_tier": b.get("capital_authority_tier"),
                "tags": _classify(b),
                "utility_score": qu,
            }
        )
    out: Dict[str, Any] = {
        "truth_version": "operator_snapshot_v1",
        "generated_at": _iso(),
        "blockers": {
            "orchestration_frozen": bool(ks.get("orchestration_frozen")),
            "kill_switch": ks,
        },
        "capital_registry_bots": len((cap.get("bots") or {})),
        "counts": {
            "total_bots": len(bots),
            "shadowish": sum(1 for r in rows if "shadow_lane" in r["tags"]),
            "stalled": sum(1 for r in rows if "stalled" in r["tags"] or "no_heartbeat" in r["tags"]),
        },
        "best_performer": best,
        "worst_performer": worst,
        "bots": rows,
    }
    p = operator_snapshot_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return out
