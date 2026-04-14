"""
Automation scope map: schedules, CLI-only actions, heartbeat health, MacBook-off behavior.

Does not start processes; heartbeats are recorded by automation entrypoints when they run.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

from trading_ai.automation.risk_bucket import runtime_root
from trading_ai.ops.automation_heartbeat import heartbeat_status


def build_automation_scope_snapshot() -> Dict[str, Any]:
    """
    Machine-readable automation / schedule map.

    External services (Telegram, Kalshi, etc.) remain reachable from the internet when
    the local runtime is off, but this codebase does not execute on the MacBook unless
    a process is running elsewhere.
    """
    rt = str(runtime_root())
    sched = os.environ.get("SCHEDULE_INTERVAL_MINUTES", "")

    jobs_immediate: List[Dict[str, Any]] = [
        {
            "id": "manual_cli",
            "trigger": "on_demand",
            "examples": [
                "python -m trading_ai run",
                "python -m trading_ai phase8 ...",
                "python -m trading_ai consistency status",
            ],
        },
        {
            "id": "serve_api",
            "trigger": "manual",
            "note": "HTTP API when operator starts serve-api",
        },
    ]

    morning_evening: List[Dict[str, Any]] = [
        {
            "id": "vault_morning_cycle",
            "trigger": "manual_or_os_scheduler",
            "script": "~/ezras-runtime/system/scripts/run_morning_cycle.py",
            "cli": "python -m trading_ai vault-cycle morning",
        },
        {
            "id": "vault_evening_cycle",
            "trigger": "manual_or_os_scheduler",
            "script": "~/ezras-runtime/system/scripts/run_evening_cycle.py",
            "cli": "python -m trading_ai vault-cycle evening",
        },
    ]

    scheduled_pipeline: List[Dict[str, Any]] = []
    if sched.strip():
        scheduled_pipeline.append(
            {
                "id": "pipeline_interval",
                "trigger": f"every_{sched.strip()}_minutes",
                "entry": "python -m trading_ai schedule",
                "requires": "long_running_process",
            }
        )
    else:
        scheduled_pipeline.append(
            {
                "id": "pipeline_interval",
                "trigger": "disabled",
                "note": "SCHEDULE_INTERVAL_MINUTES unset — scheduler not configured via env",
            }
        )

    stops_when_macbook_off = [
        "local_scheduler_daemon",
        "serve_api_local_process",
        "any_cli_not_running",
        "memory_harness_json_writes_local",
        "consistency_engine_local_audit_append",
        "post_trade_hub_local",
    ]

    external_when_local_off = [
        "telegram_cloud_delivery_if_something_else_posts",
        "kalshi_exchange_state_independent",
        "third_party_webhooks_only_if_configured_outside_this_mac",
    ]

    degradation_tiers = [
        {
            "tier": 1,
            "label": "full_local_and_external",
            "behavior": "all configured APIs + local runtime active",
        },
        {
            "tier": 2,
            "label": "local_only_airplane",
            "behavior": "no outbound APIs; local files still available",
        },
        {
            "tier": 3,
            "label": "macbook_off",
            "behavior": "no local automation; external vendor services exist but this repo does not run",
        },
        {
            "tier": 4,
            "label": "full_outage",
            "behavior": "no local and no external reachability from operator vantage",
        },
    ]

    dependency_graph: List[Dict[str, Any]] = [
        {"node": "pipeline_run", "depends_on": ["settings_env", "market_apis_optional"]},
        {"node": "live_execution", "depends_on": ["phase8_gates", "kalshi_credentials", "risk_state"]},
        {"node": "telegram_alerts", "depends_on": ["telegram_env", "apprise_optional"]},
        {"node": "memory_persistence", "depends_on": ["harness_json_paths", "optional_postgres"]},
    ]

    hb = heartbeat_status()
    return {
        "runtime_root": rt,
        "schedule_env": {"SCHEDULE_INTERVAL_MINUTES": sched or None},
        "runs_immediately": jobs_immediate,
        "morning_evening": morning_evening,
        "interval_scheduled": scheduled_pipeline,
        "manual_cli_only": [
            "repo-readiness",
            "smoke-system",
            "consistency baseline/diff",
            "memory-harness export",
        ],
        "local_only_execution": True,
        "stops_when_macbook_off": stops_when_macbook_off,
        "external_services_when_local_runtime_off": external_when_local_off,
        "degradation_tiers": degradation_tiers,
        "execution_dependency_graph": dependency_graph,
        "automation_heartbeat": hb,
        "heartbeat_health": hb.get("overall"),
        "stale_or_unknown_components": hb.get("stale_or_unknown_components", []),
    }
