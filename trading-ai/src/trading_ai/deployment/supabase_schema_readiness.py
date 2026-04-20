"""
Static + remote verification that Supabase ``trade_events`` (and critical mirrors) match app expectations.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from trading_ai.deployment.deployment_models import iso_now
from trading_ai.deployment.paths import deployment_data_dir
from trading_ai.deployment.supabase_url_diagnostics import (
    build_supabase_runtime_diagnostics,
    hypothesis_for_schema_failure,
)
from trading_ai.nte.databank.databank_schema import merge_defaults, row_for_supabase_trade_events
from trading_ai.nte.databank.supabase_error_classify import classify_postgrest_exception


def _repo_supabase_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "supabase"


# (filename, purpose, breaks_if, critical)
MIGRATION_FILES: Tuple[Tuple[str, str, str, bool], ...] = (
    (
        "trade_intelligence_databank.sql",
        "Core trade_events + rollup tables",
        "All trade_events upserts and databank aggregates fail.",
        True,
    ),
    (
        "edge_validation_engine.sql",
        "edge_id columns + edge_registry",
        "Edge columns and edge_registry mirror unavailable.",
        True,
    ),
    (
        "trade_events_acco_columns.sql",
        "ACCO / spot / options extension columns",
        "Extended columns used by merge_defaults may fail on insert.",
        True,
    ),
    (
        "balance_snapshots_milestones.sql",
        "balance_snapshots + milestones",
        "Treasury mirror only.",
        False,
    ),
    (
        "lessons_progression.sql",
        "lessons + progression + ceo_briefings",
        "Learning mirror only.",
        False,
    ),
)


def required_migrations_ordered() -> List[str]:
    return [t[0] for t in MIGRATION_FILES]


def required_migrations_critical() -> List[str]:
    return [t[0] for t in MIGRATION_FILES if t[3]]


def _required_trade_events_select_list() -> str:
    raw = {
        "trade_id": "__schema_probe__",
        "avenue_id": "A",
        "avenue_name": "coinbase",
        "asset": "BTC-USD",
        "strategy_id": "schema_probe",
        "route_chosen": "A",
        "regime": "probe",
        "timestamp_open": "2020-01-01T00:00:00+00:00",
        "timestamp_close": "2020-01-01T00:00:00+00:00",
    }
    m = merge_defaults(raw)
    row = row_for_supabase_trade_events(m, {})
    return ",".join(sorted(row.keys()))


def _inventory_migrations() -> Tuple[bool, List[Dict[str, Any]], List[str]]:
    root = _repo_supabase_dir()
    details: List[Dict[str, Any]] = []
    missing: List[str] = []
    for fname, purpose, breaks_if, critical in MIGRATION_FILES:
        p = root / fname
        ok = p.is_file() and p.stat().st_size > 10
        details.append(
            {
                "file": fname,
                "critical": critical,
                "path": str(p),
                "present": ok,
                "purpose": purpose,
                "what_breaks_if_missing": breaks_if,
            }
        )
        if not ok:
            missing.append(fname)
    inventory_ok = len(missing) == 0
    return inventory_ok, details, missing


def _parse_missing_objects_from_error(msg: str) -> List[str]:
    out: List[str] = []
    if not msg:
        return out
    for m in re.finditer(r'column\s+"?(\w+)"?\s+does not exist', msg, re.I):
        out.append(f"trade_events.missing_column:{m.group(1)}")
    for m in re.finditer(r'relation\s+"?(\w+)"?\s+does not exist', msg, re.I):
        out.append(f"missing_relation:{m.group(1)}")
    if not out and msg:
        out.append("trade_events:remote_probe_failed")
    return out


def _verify_remote_tables() -> Tuple[bool, str, Dict[str, Any], List[str]]:
    """trade_events columns + edge_registry presence."""
    from trading_ai.nte.databank.supabase_trade_sync import _client_with_source

    client, key_src = _client_with_source()
    meta: Dict[str, Any] = {"client": bool(client), "key_source": key_src}
    missing_objects: List[str] = []

    if not client:
        return False, "no_supabase_client_cannot_verify_remote_schema", meta, ["supabase:client_unavailable"]

    sel = _required_trade_events_select_list()
    try:
        client.table("trade_events").select(sel).limit(1).execute()
        meta["trade_events_column_probe"] = "ok"
    except Exception as exc:
        detail = str(exc)
        meta["error"] = type(exc).__name__
        meta["error_detail"] = detail[:1200]
        cls = classify_postgrest_exception(exc)
        meta["error_classification"] = {
            "category": cls["category"],
            "fix_scope": cls["fix_scope"],
            "operator_hint": cls["operator_hint"],
            "http_status_guess": cls.get("http_status_guess"),
        }
        missing_objects.extend(_parse_missing_objects_from_error(detail))
        missing_objects.append(f"classification:{cls['category']}")
        return False, f"trade_events_probe:{type(exc).__name__}", meta, missing_objects

    try:
        client.table("edge_registry").select("edge_id").limit(1).execute()
        meta["edge_registry_probe"] = "ok"
    except Exception as exc:
        detail = str(exc)
        meta["edge_registry_error"] = type(exc).__name__
        meta["edge_registry_error_detail"] = detail[:800]
        cls = classify_postgrest_exception(exc)
        meta["edge_registry_classification"] = {
            "category": cls["category"],
            "fix_scope": cls["fix_scope"],
            "operator_hint": cls["operator_hint"],
        }
        missing_objects.append("edge_registry:table_or_permission")
        missing_objects.append(f"classification:{cls['category']}")
        return False, f"edge_registry_probe:{type(exc).__name__}", meta, missing_objects

    return True, "remote_schema_ok", meta, []


def run_supabase_schema_readiness(*, write_file: bool = True) -> Dict[str, Any]:
    """
    ``schema_ready`` / ``supabase_schema_ready``: repo migrations present and remote objects match.

    Output includes ``required_migrations``, ``missing_remote_objects``, ``schema_ready``.
    """
    deployment_data_dir().mkdir(parents=True, exist_ok=True)
    inv_ok, inv_details, missing_files = _inventory_migrations()

    allow_skip = (os.environ.get("SUPABASE_SCHEMA_CHECK_SKIP") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )

    remote_ok = False
    remote_reason = "not_attempted"
    remote_meta: Dict[str, Any] = {}
    missing_remote_objects: List[str] = []

    if inv_ok and not allow_skip:
        remote_ok, remote_reason, remote_meta, missing_remote_objects = _verify_remote_tables()
    elif allow_skip:
        remote_ok = True
        remote_reason = "skipped_by_env_SUPABASE_SCHEMA_CHECK_SKIP"
        remote_meta = {"warning": "remote_schema_not_verified"}
        missing_remote_objects = ["remote:verification_skipped_by_SUPABASE_SCHEMA_CHECK_SKIP"]

    if not inv_ok:
        missing_remote_objects.append(f"repo_missing_files:{','.join(missing_files)}")

    schema_ready = bool(inv_ok and remote_ok)

    reasons: List[str] = []
    if not inv_ok:
        reasons.append("missing_migration_files:" + ",".join(missing_files))
    if not remote_ok and not allow_skip:
        reasons.append(remote_reason)

    issue_kind = "ok"
    if not inv_ok:
        issue_kind = "repo_missing_migration_files"
    elif not remote_ok and not allow_skip:
        rs = (remote_meta.get("error_classification") or {}).get("fix_scope") or ""
        if rs in ("manual_sql_migration", "manual_migration_or_wrong_project_url"):
            issue_kind = "manual_database_or_project_mismatch"
        elif rs == "credentials_or_rls_manual":
            issue_kind = "manual_credentials_or_rls"
        elif rs == "runtime_env":
            issue_kind = "runtime_network_or_env"
        else:
            issue_kind = "remote_probe_failed"

    url_diag = build_supabase_runtime_diagnostics()
    cls_cat = None
    msg_ex = ""
    if isinstance(remote_meta, dict):
        ec = remote_meta.get("error_classification")
        if isinstance(ec, dict):
            cls_cat = ec.get("category")
        msg_ex = str(remote_meta.get("error_detail") or remote_meta.get("edge_registry_error_detail") or "")
    failure_hypothesis = hypothesis_for_schema_failure(
        remote_ok=remote_ok,
        category=str(cls_cat) if cls_cat else None,
        message_excerpt=msg_ex,
    )

    out: Dict[str, Any] = {
        "generated_at": iso_now(),
        "required_migrations": required_migrations_ordered(),
        "required_migrations_critical": required_migrations_critical(),
        "combined_migration_file_repo": str(_repo_supabase_dir() / "ALL_REQUIRED_LIVE_MIGRATIONS.sql"),
        "migration_inventory_ok": inv_ok,
        "migration_files": inv_details,
        "migration_order_file": str(_repo_supabase_dir() / "MIGRATION_ORDER.txt"),
        "missing_remote_objects": missing_remote_objects,
        "remote_schema_verified": remote_ok,
        "remote_verify_reason": remote_reason,
        "remote_verify_meta": remote_meta,
        "supabase_url_runtime": url_diag,
        "failure_hypothesis_operator": failure_hypothesis,
        "schema_ready": schema_ready,
        "supabase_schema_ready": schema_ready,
        "blocking_reasons": reasons,
        "issue_kind": issue_kind,
        "required_trade_events_column_count": len(_required_trade_events_select_list().split(",")),
    }

    outp = deployment_data_dir() / "supabase_schema_readiness.json"
    if write_file:
        outp.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    return out
