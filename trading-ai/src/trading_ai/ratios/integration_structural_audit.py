"""
Structural integration map — audit-first classification (see ``build_structural_integration_audit``).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

# Curated from codebase review — update when components change materially.
_COMPONENTS: List[Dict[str, Any]] = [
    {
        "id": "deployable_capital_report",
        "area": "A",
        "paths": ["trading_ai/nte/execution/routing/integration/capital_reports.py"],
        "classification": "already_live_and_authoritative",
        "notes": "Written on validation preflight when control artifacts enabled.",
    },
    {
        "id": "reserve_capital_report",
        "area": "A",
        "paths": ["trading_ai/ratios/reserve_compute.py"],
        "classification": "partially_built_needs_extension",
        "notes": "Derives from deployable + ratio bundle; extend when live balances stream in.",
    },
    {
        "id": "universal_runtime_policy",
        "area": "O",
        "paths": ["trading_ai/nte/execution/routing/policy/universal_runtime_policy.py"],
        "classification": "already_live_and_authoritative",
        "notes": "Layered on CoinbaseRuntimeProductPolicy.",
    },
    {
        "id": "validation_resolve_coherent",
        "area": "O",
        "paths": ["trading_ai/nte/execution/routing/integration/validation_resolve.py"],
        "classification": "already_live_and_authoritative",
        "notes": "Single-leg execution; multi-leg search diagnostics only.",
    },
    {
        "id": "adaptive_operating_system",
        "area": "N",
        "paths": ["trading_ai/control/adaptive_routing_live.py", "trading_ai/control/live_adaptive_integration.py"],
        "classification": "already_live_and_authoritative",
        "notes": "Used in micro-validation preamble and Avenue A gates.",
    },
    {
        "id": "ceo_daily_review_generic",
        "area": "L",
        "paths": ["trading_ai/review/ceo_review_session.py"],
        "classification": "already_live_and_authoritative",
        "notes": "General CEO review from daily_diagnosis — not ratio-specific.",
    },
    {
        "id": "daily_ratio_review",
        "area": "L",
        "paths": ["trading_ai/ratios/daily_ratio_review.py"],
        "classification": "partially_built_needs_extension",
        "notes": "Ratio-focused session; references same memory paths; no separate LLM orchestration.",
    },
    {
        "id": "gpt_researcher_pipeline",
        "area": "M",
        "paths": ["trading_ai/intake/gpt_researcher_hooks.py"],
        "classification": "already_built_but_not_wired",
        "notes": "Optional enrichment for market research — not CEO ratio orchestration.",
    },
    {
        "id": "trade_events_databank",
        "area": "P",
        "paths": ["trading_ai/nte/databank/databank_schema.py", "trading_ai/nte/databank/local_trade_store.py"],
        "classification": "already_live_and_authoritative",
        "notes": "ratio_context folded into market_snapshot_json for compatibility.",
    },
    {
        "id": "position_sizing_shark",
        "area": "C",
        "paths": ["trading_ai/governance/position_sizing_policy.py", "trading_ai/shark/capital_phase.py"],
        "classification": "already_live_and_authoritative",
        "notes": "Kalshi / shark path — separate from NTE ratio bundle.",
    },
    {
        "id": "nte_coinbase_sizing",
        "area": "C",
        "paths": ["trading_ai/nte/config/settings.py"],
        "classification": "already_live_and_authoritative",
        "notes": "Feeds universal ratio registry.",
    },
    {
        "id": "gate_hooks_shared",
        "area": "H",
        "paths": ["trading_ai/nte/execution/routing/integration/gate_hooks.py"],
        "classification": "partially_built_needs_extension",
        "notes": "Extended with ratio bundle reader.",
    },
]


def build_structural_integration_audit(*, runtime_root: Path) -> Dict[str, Any]:
    return {
        "artifact": "structural_integration_audit",
        "version": "v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(runtime_root),
        "components": _COMPONENTS,
        "classification_legend": {
            "already_live_and_authoritative": "Used on live or validation paths with clear source of truth.",
            "already_built_but_not_wired": "Code exists; not invoked in default flows.",
            "partially_built_needs_extension": "Functional subset; more wiring or data needed.",
            "placeholder_or_scaffold_only": "Explicit scaffold — do not treat as live risk control.",
            "missing": "Not present in repo.",
        },
    }


def write_integration_audit_artifacts(runtime_root: Path) -> Dict[str, str]:
    payload = build_structural_integration_audit(runtime_root=runtime_root)
    js = json.dumps(payload, indent=2, default=str)
    learn = runtime_root / "data" / "learning"
    ctrl = runtime_root / "data" / "control"
    learn.mkdir(parents=True, exist_ok=True)
    ctrl.mkdir(parents=True, exist_ok=True)
    out: Dict[str, str] = {}
    for sub, label in ((learn, "learning"), (ctrl, "control")):
        jp = sub / "integration_structural_audit.json"
        tp = sub / "integration_structural_audit.txt"
        jp.write_text(js, encoding="utf-8")
        tp.write_text("STRUCTURAL INTEGRATION AUDIT\n============================\n" + js[:20000] + "\n", encoding="utf-8")
        out[f"integration_structural_audit_json_{label}"] = str(jp)
    return out
