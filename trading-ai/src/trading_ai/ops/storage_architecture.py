"""
Explicit storage architecture map: local vs runtime vs package data vs external services.

Read-only introspection for operators and auditors. Does not move or encrypt data.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.automation.risk_bucket import runtime_root
from trading_ai.governance.audit_chain import chain_path as governance_chain_path
from trading_ai.memory_harness.paths_harness import harness_data_dir
from trading_ai.security.encryption_at_rest import default_protected_paths, encryption_operational_status


def _trading_ai_package_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _memory_storage_mode() -> str:
    use_pg = os.environ.get("MEMORY_HARNESS_USE_POSTGRES", "0") in ("1", "true", "True")
    use_r = os.environ.get("MEMORY_HARNESS_USE_REDIS", "0") in ("1", "true", "True")
    if use_pg or use_r:
        return "routed_json_fallback_operator_owned"
    return "local_json_operator_owned"


def build_storage_snapshot(
    *,
    settings: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Structured map of persistence layers (inspectable JSON).

    ``settings``: optional ``Settings`` for resolved paths (data_dir, metrics path).
    """
    rt = runtime_root()
    pkg = _trading_ai_package_root()
    pkg_data = pkg / "data"

    local_state_files: List[str] = [
        str(rt / "state" / "risk_state.json"),
        str(rt / "state" / "parameter_governance_state.json"),
        str(rt / "state" / "consistency_baseline.json"),
        str(rt / "state" / "operator_registry.json"),
        str(rt / "state" / "temporal_consistency_state.json"),
        str(rt / "state" / "automation_heartbeat_state.json"),
    ]

    local_logs: List[str] = [
        str(rt / "logs" / "parameter_governance_log.md"),
        str(rt / "logs" / "consistency_timeseries.jsonl"),
        str(governance_chain_path()),
        str(rt / "logs" / "operator_registry_log.md"),
        str(rt / "logs" / "temporal_consistency_log.md"),
        str(rt / "logs" / "automation_heartbeat_log.md"),
    ]

    package_data: List[str] = [
        str(pkg_data),
        str(harness_data_dir()),
        str(pkg_data / "truth"),
        str(pkg_data / "learning_events_log.json"),
    ]

    external_services: List[Dict[str, Any]] = [
        {"id": "telegram", "role": "alerts_operator_channel", "persistence": "vendor_cloud_chat_history"},
        {"id": "kalshi_api", "role": "venue_execution_and_truth", "persistence": "exchange_side"},
        {"id": "polymarket_gamma", "role": "market_data", "persistence": "none_local"},
        {"id": "openai_api", "role": "brief_generation_optional", "persistence": "vendor_logs_only"},
        {"id": "tavily", "role": "research_optional", "persistence": "none_local"},
        {"id": "apprise", "role": "multi_channel_alerts", "persistence": "none_local"},
    ]

    if os.environ.get("MEMORY_HARNESS_DATABASE_URL"):
        external_services.append(
            {
                "id": "memory_postgres",
                "role": "optional_memory_backend",
                "persistence": "remote_when_enabled",
            }
        )

    remote_persistence_enabled = bool(os.environ.get("MEMORY_HARNESS_DATABASE_URL"))

    remote_dependencies: List[str] = []
    if remote_persistence_enabled:
        remote_dependencies.append("postgres_memory_harness_optional")
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        remote_dependencies.append("telegram_bot_api")
    if os.environ.get("KALSHI_API_KEY"):
        remote_dependencies.append("kalshi_rest_api")

    metrics_path = ""
    sqlite_note = ""
    if settings is not None:
        try:
            metrics_path = str(Path(settings.metrics_json_path).resolve())
        except Exception:
            metrics_path = ""
        try:
            dd = getattr(settings, "data_dir", None)
            if dd is not None:
                sqlite_note = str((Path(dd) / "trading_ai.sqlite").resolve())
        except Exception:
            pass

    extra_local: List[str] = []
    if metrics_path:
        extra_local.append(metrics_path)
    if sqlite_note:
        extra_local.append(sqlite_note)
        local_state_files.append(sqlite_note)

    return {
        "runtime_root": str(rt),
        "local_state_files": sorted(set(local_state_files)),
        "local_logs": sorted(set(local_logs)),
        "package_data": sorted(set(package_data)),
        "external_services": external_services,
        "remote_persistence_enabled": remote_persistence_enabled,
        "remote_dependencies": remote_dependencies,
        "memory_storage_mode": _memory_storage_mode(),
        "deployment_mode": "local_first",
        "memory_harness_paths": {
            "harness_data_dir": str(harness_data_dir()),
            "export_import": "memory-harness export / import CLI; see memory_harness.sync",
            "postgres_env": "MEMORY_HARNESS_USE_POSTGRES + MEMORY_HARNESS_DATABASE_URL",
            "redis_env": "MEMORY_HARNESS_USE_REDIS",
        },
        "metrics_and_sqlite": extra_local,
        "governance_audit": {
            "tamper_evident_chain_path": str(governance_chain_path()),
            "verification_cli": "embedded in consistency status and integrity-check",
        },
        "encryption_at_rest": encryption_operational_status(),
        "protected_paths_when_encryption_enabled": default_protected_paths(rt),
        "not_yet_remote": [
            "governance_chain_jsonl_replica_off_box",
            "parameter_governance_state",
            "risk_state_json",
            "default_json_memory_namespaces",
        ],
        "future_remote_candidates": [
            "append_only_audit_to_object_storage_or_postgres",
            "memory_namespaces_to_postgres",
            "artifact_blob_to_s3_compatible",
        ],
    }


def build_memory_storage_map() -> Dict[str, Any]:
    """Explicit memory harness storage documentation."""
    mode = _memory_storage_mode()
    return {
        "primary_local_paths": {
            "harness_data_dir": str(harness_data_dir()),
            "json_memory_dir": str(harness_data_dir() / "memory"),
            "metadata": str(harness_data_dir() / "metadata"),
            "exports": str(harness_data_dir() / "exports"),
        },
        "remote_backing": {
            "postgres": os.environ.get("MEMORY_HARNESS_USE_POSTGRES", "0"),
            "redis": os.environ.get("MEMORY_HARNESS_USE_REDIS", "0"),
            "dsn_set": bool(os.environ.get("MEMORY_HARNESS_DATABASE_URL")),
        },
        "export_import": {
            "cli": "python -m trading_ai memory-harness export | import",
            "dual_write": os.environ.get("MEMORY_HARNESS_JSON_DUAL_WRITE", "1"),
        },
        "always_on_changes": [
            "dedicated_postgres_or_managed_db",
            "optional_redis_for_ephemeral_namespaces",
            "worker_process_independent_of_macbook",
            "scheduler_cron_or_queue_with_health_checks",
        ],
    }


def build_remote_readiness_plan() -> Dict[str, Any]:
    """Structured future deployment map (no cloud provisioning)."""
    return {
        "current_state": "local_first_macbook_runtime_with_optional_postgres_memory",
        "target_control_plane": {
            "doctrine_registry": "postgres_table_or_kv_with_signed_versions",
            "audit_log": "append_only_s3_or_postgres_partitioned",
            "heartbeat_monitor": "always_on_service",
            "consistency_engine_api": "read_only_http_grpc",
        },
        "target_execution_tier": {
            "bots": "ephemeral_workers_macbook_or_vps",
            "schedulers": "celery_apscheduler_k8s_cron",
        },
        "target_data_tier": {
            "postgres": [
                "trade_state",
                "memory_long_term_namespaces",
                "governance_audit_pointers",
            ],
            "object_storage": [
                "large_research_artifacts",
                "model_weights",
                "exported_memory_archives",
            ],
            "redis": ["ephemeral_inter_agent_coordination_optional"],
        },
        "stays_local_dev_only": [
            "developer_venv",
            "scratch_notebooks",
            "uncommitted_experiments",
        ],
        "migration_risk_matrix": [
            {
                "data_class": "memory_json",
                "risks": ["partial_copy", "dual_write_skew"],
                "rollback": "restore_from_export_zip_and_json_dual_write",
            },
            {
                "data_class": "audit_jsonl",
                "risks": ["line_loss_during_move", "clock_skew"],
                "rollback": "replay_from_immutable_backup_bucket",
            },
        ],
    }
