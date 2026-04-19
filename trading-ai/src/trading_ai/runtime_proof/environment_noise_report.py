"""
Classify environment / API / billing noise vs organism core failures for bundle and harness runs.

Nonblocking noise must not mask real integrity failures; real failures still fail hard upstream.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


def classify_env_noise_context(
    *,
    stress_used_skip_models: bool = True,
    bundle_preflight: bool = False,
) -> Dict[str, Any]:
    """
    Heuristic flags for reports. Operators should treat ``*_noise_present`` as diagnostic, not as PASS/FAIL.
    """
    anthropic = bool((os.environ.get("ANTHROPIC_API_KEY") or "").strip())
    openai = bool((os.environ.get("OPENAI_API_KEY") or "").strip())
    stub = (os.environ.get("ORGANISM_REVIEW_STUB_ONLY") or "").strip().lower() in ("1", "true", "yes")

    ext_api: List[str] = []
    neutralized: List[str] = []
    if stub:
        neutralized.append("ORGANISM_REVIEW_STUB_ONLY_requests_stub_review_paths_where_supported")
    if stress_used_skip_models:
        neutralized.append("stress_soak_skip_models_forces_stubbed_claude_gpt_in_explicit_cycles")
    # True external noise risk: keys present and no neutralization for review ticks.
    if (anthropic or openai) and not stub and not stress_used_skip_models:
        ext_api.append("live_model_keys_present_scheduler_or_reviews_may_hit_network")

    billing: List[str] = []
    if anthropic and not stub:
        billing.append("anthropic_key_set_quota_or_billing_may_surface")
    if openai and not stub:
        billing.append("openai_key_set_quota_or_billing_may_surface")

    return {
        "schema": "environment_noise_report_v1",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "external_api_noise_present": len(ext_api) > 0,
        "external_api_noise_detail": ext_api,
        "noise_neutralization": neutralized,
        "billing_or_quota_noise_present": len(billing) > 0,
        "billing_or_quota_noise_detail": billing,
        "environment_noise_nonblocking": bool(stress_used_skip_models or stub or len(ext_api) == 0),
        "bundle_preflight_context": bundle_preflight,
        "classification_notes": {
            "harmless_informational": "Stub reviews, skip_models stress, missing optional keys when not used.",
            "should_warn": "Live API keys present while running proof harnesses that may call billing.",
            "should_fail_the_run": "Reserved for true organism integrity / governance enforcement failures (handled by callers).",
        },
    }


def write_environment_noise_report(
    runtime_root: Path,
    *,
    stress_used_skip_models: bool = True,
    bundle_preflight: bool = False,
) -> Path:
    runtime_root = runtime_root.resolve()
    d = runtime_root / "noise_proof"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "environment_noise_report.json"
    p.write_text(
        json.dumps(
            classify_env_noise_context(
                stress_used_skip_models=stress_used_skip_models,
                bundle_preflight=bundle_preflight,
            ),
            indent=2,
        ),
        encoding="utf-8",
    )
    return p
