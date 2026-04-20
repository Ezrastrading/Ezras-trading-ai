"""
Honest static audit: which subsystems are runtime-invoked vs artifact-only vs advisory.

Does not execute venue APIs — classification is code-structure + entrypoint references.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from trading_ai.runtime_paths import ezras_runtime_root


def _rows() -> List[Dict[str, Any]]:
    return [
        {
            "component": "resolve_validation_product_coherent",
            "scope": "coinbase_nte_preflight",
            "current_state": "runtime_invoked_on_validation_preflight",
            "runtime_entrypoints": [
                "trading_ai.deployment.live_micro_validation",
                "trading_ai.deployment.validation_products",
                "scripts/validation_product_diagnostic.py",
            ],
            "safe_to_invoke": True,
            "action_taken": "audited",
        },
        {
            "component": "gate_ratio_and_reserve_bundle",
            "scope": "gate_a_gate_b_read",
            "current_state": "advisory_runtime_context",
            "runtime_entrypoints": ["trading_ai.nte.execution.routing.integration.gate_hooks"],
            "safe_to_invoke": True,
            "action_taken": "audited",
        },
        {
            "component": "multi_avenue_control_bundle",
            "scope": "system",
            "current_state": "invoked_via_cli_or_ratios_write_everything",
            "runtime_entrypoints": ["python -m trading_ai.multi_avenue snapshot"],
            "safe_to_invoke": True,
            "action_taken": "audited",
        },
        {
            "component": "kalshi_simple_scanner",
            "scope": "gate_b",
            "current_state": "module_present_scheduler_dependent",
            "runtime_entrypoints": ["trading_ai.shark.run_shark (if scheduled)"],
            "safe_to_invoke": True,
            "action_taken": "explicit_not_continuous_in_core_nte",
        },
    ]


def build_runtime_invocation_audit(*, runtime_root: Path | None = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    return {
        "artifact": "runtime_invocation_audit",
        "version": "v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(root),
        "honesty": "Static classification — verify host process maps with scheduler for scanners.",
        "components": _rows(),
    }


def write_runtime_invocation_audit(*, runtime_root: Path | None = None) -> Dict[str, str]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = root / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    payload = build_runtime_invocation_audit(runtime_root=root)
    js = json.dumps(payload, indent=2, default=str)
    jp = ctrl / "runtime_invocation_audit.json"
    tp = ctrl / "runtime_invocation_audit.txt"
    jp.write_text(js, encoding="utf-8")
    tp.write_text(js[:28000] + "\n", encoding="utf-8")
    return {"runtime_invocation_audit_json": str(jp), "runtime_invocation_audit_txt": str(tp)}
