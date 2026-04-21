"""Bootstrap governance and capability maturity JSON under data/control — advisory, operator-owned."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.intelligence.paths import (
    intelligence_capability_maturity_json_path,
    intelligence_governance_json_path,
)


def default_intelligence_governance() -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "updated_at": "",
        "truth_origin": "bootstrap_default",
        "bootstrap_default": True,
        "operator_replaced": False,
        "deployment_specific": False,
        "live_proven": False,
        "operator_next_step": "Replace or extend this file with deployment-specific governance when operators lock policy.",
        "what_ai_may_auto_do": [
            "Create structured internal tickets from detectors",
            "Route tickets to explicit domains and append routing logs",
            "Generate CEO review artifacts that restate evidence-bound facts",
            "Append additive learning updates when ticket + confidence thresholds are met",
            "Run daily clustering and research-queue suggestions",
        ],
        "what_requires_operator_review": [
            "Any change to live trading permissions or sizing",
            "Policy files that gate venues, products, or capital",
            "Execution code changes affecting order placement",
            "Lowering safety thresholds or bypassing gates",
        ],
        "what_is_advisory_only": [
            "CEO sessions and daily learning narratives",
            "Research tickets and domain priority lists",
            "Opportunity detections without independent external validation",
        ],
        "what_is_forbidden": [
            "Silent expansion of execution authority",
            "Removing or weakening safety constraints without recorded approval",
            "Autonomous trading outside configured paths",
            "Concealing uncertainty or fabricating market mastery",
            "Direct mutation of execution code without review gates",
        ],
        "operator_supervision_required": True,
    }


def default_capability_maturity() -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "updated_at": "",
        "truth_origin": "bootstrap_default",
        "bootstrap_default": True,
        "operator_replaced": False,
        "deployment_specific": False,
        "live_proven": False,
        "operator_next_step": "Set current_level and evidence-backed milestones when deployment maturity is known.",
        "current_level": "structured_analyst",
        "levels": {
            "observer": {"description": "Collects events without interpretation depth."},
            "structured_analyst": {"description": "Tickets, routes, and summarizes with schemas."},
            "domain_learner": {"description": "Evidence-gated domain file updates."},
            "strategy_reviewer": {"description": "Can compare strategies with operator oversight."},
            "supervised_operator_assistant": {"description": "Proposes actions; operator approves."},
            "constrained_execution_strategist": {"description": "Never autonomous — proposals only."},
            "not_authorized_for_autonomous_trading": {
                "description": "Hard stop — no unsupervised live trading authority."
            },
        },
        "honesty_note": "Maturity tracks analytical scaffolding, not trading autonomy.",
    }


def ensure_intelligence_control_artifacts(runtime_root: Optional[Path] = None) -> Dict[str, str]:
    """Write defaults if missing — never overwrite operator edits."""
    now = datetime.now(timezone.utc).isoformat()
    gp = intelligence_governance_json_path(runtime_root=runtime_root)
    cp = intelligence_capability_maturity_json_path(runtime_root=runtime_root)
    gp.parent.mkdir(parents=True, exist_ok=True)
    out: Dict[str, str] = {}
    if not gp.exists():
        g = default_intelligence_governance()
        g["updated_at"] = now
        gp.write_text(json.dumps(g, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        out["governance"] = str(gp)
    if not cp.exists():
        c = default_capability_maturity()
        c["updated_at"] = now
        cp.write_text(json.dumps(c, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        out["capability_maturity"] = str(cp)
    return out


def load_governance(runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    ensure_intelligence_control_artifacts(runtime_root=runtime_root)
    return json.loads(intelligence_governance_json_path(runtime_root=runtime_root).read_text(encoding="utf-8"))


def is_action_forbidden(action: str, runtime_root: Optional[Path] = None) -> bool:
    """Return True if ``action`` matches forbidden governance phrases (substring match)."""
    gov = load_governance(runtime_root=runtime_root)
    a = action.lower()
    for phrase in gov.get("what_is_forbidden", []):
        if str(phrase).lower() in a:
            return True
    return False
