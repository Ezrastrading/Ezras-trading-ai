"""Operator-facing runbooks under ``data/deployment`` (written by deployment checklist)."""

from __future__ import annotations

from pathlib import Path

from trading_ai.deployment.paths import deployment_data_dir
from trading_ai.deployment.supabase_url_diagnostics import repo_all_migrations_sql_hint
from trading_ai.governance.storage_architecture import global_memory_dir
from trading_ai.runtime_paths import ezras_runtime_root


def supabase_manual_apply_runbook_path() -> Path:
    return deployment_data_dir() / "supabase_manual_apply_runbook.txt"


def governance_manual_fix_path() -> Path:
    return deployment_data_dir() / "governance_manual_fix.txt"


def live_env_manual_fix_path() -> Path:
    return deployment_data_dir() / "live_env_manual_fix.txt"


def write_supabase_manual_apply_runbook_txt() -> Path:
    p = supabase_manual_apply_runbook_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[3]
    mig = root / "supabase" / "ALL_REQUIRED_LIVE_MIGRATIONS.sql"
    p.write_text(
        "\n".join(
            [
                "SUPABASE — MANUAL APPLY RUNBOOK (Ezras Trading AI)",
                "=" * 60,
                "",
                "WHAT YOU MUST DO (cannot be done by Python alone):",
                "  1) Open Supabase Dashboard for the PROJECT that matches your live SUPABASE_URL.",
                "  2) Open SQL Editor.",
                "  3) Paste the ACTUAL SQL STATEMENTS from the repo files — not the filename as text.",
                "",
                "ORDER (required):",
                "  Step A — trade_intelligence_databank.sql",
                "  Step B — edge_validation_engine.sql",
                "  Step C — trade_events_acco_columns.sql",
                "",
                "OR run the single combined file (same order, idempotent):",
                f"  {mig}",
                "",
                "WHY EACH STEP:",
                "  A) Creates public.trade_events and summary tables — without A, all upserts 404/fail.",
                "  B) Adds edge_id / edge_lane / market_snapshot_json + edge_registry — app sends these columns.",
                "  C) Adds ACCO / spot / options / latency columns — merge_defaults may send these fields.",
                "",
                "VERIFY AFTER EACH STEP (optional) or ONCE AT END:",
                "  SELECT * FROM public.trade_events LIMIT 1;",
                "",
                "EXPECTED:",
                "  - No error. Table exists; result may be 0 rows (empty table is OK).",
                "  - If ERROR: read the message — missing table vs missing column tells you which step failed.",
                "",
                "THEN IN YOUR RUNTIME:",
                "  - SUPABASE_URL must equal Dashboard → Settings → API → Project URL exactly.",
                "  - SUPABASE_KEY or SUPABASE_SERVICE_ROLE_KEY must be from THAT same project.",
                "",
                f"Combined SQL in repo: {repo_all_migrations_sql_hint()}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return p


def write_governance_manual_fix_txt() -> Path:
    p = governance_manual_fix_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    jr = global_memory_dir() / "joint_review_latest.json"
    root = ezras_runtime_root()
    p.write_text(
        "\n".join(
            [
                "GOVERNANCE — MANUAL REFERENCE (Ezras Trading AI)",
                "=" * 60,
                "",
                f"EXPECTED FILE PATH (runtime): {jr}",
                f"  (under EZRAS_RUNTIME_ROOT={root})",
                "",
                "GOVERNANCE_ORDER_ENFORCEMENT=true requires a joint review row that passes the gate, OR the",
                "automatic bootstrap (default ON: GOVERNANCE_JOINT_BOOTSTRAP=1) writes a safe default on first load.",
                "",
                "MINIMUM VALID JSON (example — fields the gate reads):",
                '  {',
                '    "schema_version": "1.0",',
                '    "ts": "2026-01-01T12:00:00+00:00",',
                '    "generated_at": "2026-01-01T12:00:00+00:00",',
                '    "joint_review_id": "my_review_001",',
                '    "packet_id": "packet_ref_optional",',
                '    "live_mode_recommendation": "normal",',
                '    "review_integrity_state": "full",',
                '    "reason": "operator_signed_off",',
                '    "empty": false',
                "  }",
                "",
                "SEMANTICS:",
                "  - governance_proof_ok (in governance_proof.json): dry-run vs full gate logic agree — consistency check.",
                "  - governance_trading_permitted: under enforcement, orders are ALLOWED (check_new_order_allowed_full true).",
                "  - If trading is fail-closed: read governance_trading_block_reason in governance_proof.json.",
                "",
                "VERIFY NOT FAIL-CLOSED:",
                "  1) PYTHONPATH=src python3 -c \"from trading_ai.deployment.governance_proof import prove_governance_behavior; import json; print(json.dumps(prove_governance_behavior(write_file=False), indent=2))\"",
                "  2) governance_trading_permitted should be true after valid joint or bootstrap.",
                "",
                "IF FILE PERMISSIONS PREVENT WRITE:",
                "  - Fix host filesystem permissions on shark/memory/global — the app cannot chmod for you.",
                "  - Ensure EZRAS_RUNTIME_ROOT points to a writable directory for the process user.",
                "",
                "DISABLE AUTO-BOOTSTRAP (strict ops only): GOVERNANCE_JOINT_BOOTSTRAP=0",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return p


def write_live_env_manual_fix_txt() -> Path:
    p = live_env_manual_fix_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "\n".join(
            [
                "LIVE EXECUTION ENV — MANUAL (Ezras Trading AI)",
                "=" * 60,
                "",
                "Set these in the SAME shell or process host (Railway, systemd, docker env, etc.)",
                "before deployment checklist / micro-validation.",
                "",
                "REQUIRED (Coinbase live micro-validation streak):",
                "",
                "  export COINBASE_EXECUTION_ENABLED=true",
                "  export LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM=YES_I_UNDERSTAND_REAL_CAPITAL",
                "  export EZRAS_DRY_RUN=false",
                "",
                "NTE / Coinbase avenue live (when using NTE execution):",
                "",
                "  export NTE_EXECUTION_MODE=live",
                "  export NTE_LIVE_TRADING_ENABLED=true",
                "",
                "SUPABASE (remote trade_events sync):",
                "",
                "  export SUPABASE_URL='https://YOUR-PROJECT-REF.supabase.co'",
                "  export SUPABASE_SERVICE_ROLE_KEY='...'   # or SUPABASE_KEY per your policy",
                "",
                "GOVERNANCE (production):",
                "",
                "  export GOVERNANCE_ORDER_ENFORCEMENT=true",
                "  # Optional: GOVERNANCE_JOINT_BOOTSTRAP=1  (default) — safe default joint file if missing",
                "",
                "PAPER / DRY RUN:",
                "  - Do NOT set EZRAS_DRY_RUN=true if you intend real capital micro-validation.",
                "  - If NTE_*_PAPER or similar exists in your deployment, unset or set to live per ops doc.",
                "",
                "THEN RUN:",
                "  cd trading-ai && PYTHONPATH=src python3 -m trading_ai.deployment checklist",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return p


def write_all_operator_artifacts() -> Dict[str, str]:
    """Write runbooks; returns map name -> absolute path."""
    out: Dict[str, str] = {}
    for name, fn in (
        ("supabase_manual_apply_runbook", write_supabase_manual_apply_runbook_txt),
        ("governance_manual_fix", write_governance_manual_fix_txt),
        ("live_env_manual_fix", write_live_env_manual_fix_txt),
    ):
        p = fn()
        out[name] = str(p.resolve())
    return out
