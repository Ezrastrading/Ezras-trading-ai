"""Human-readable final readiness report (operator-facing)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from trading_ai.deployment.paths import (
    checklist_json_path,
    deployment_data_dir,
    final_readiness_path,
    governance_proof_path,
    ops_outputs_proof_path,
    reconciliation_proof_jsonl_path,
    streak_state_path,
    supabase_proof_jsonl_path,
    supabase_schema_readiness_path,
)
from trading_ai.deployment.deployment_models import iso_now


def _read_json(p: Path) -> Optional[Dict[str, Any]]:
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def _tail_jsonl(p: Path, n: int = 1) -> str:
    if not p.is_file():
        return ""
    lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not lines:
        return ""
    tail = lines[-n:]
    try:
        return json.dumps([json.loads(x) for x in tail], indent=2, default=str)
    except json.JSONDecodeError:
        return "\n".join(tail)


def _failed_runtime_steps(critical: Sequence[str]) -> List[str]:
    """Map blocker codes to operator step names (checklist / micro-validation / …)."""
    steps: List[str] = []
    for b in critical:
        if b in ("deployment_checklist_not_green", "env_parity", "trading_halt_present"):
            if "checklist" not in steps:
                steps.append("checklist")
            continue
        if b in (
            "live_validation_streak_not_passed",
            "streak_partial_failures_recorded",
            "missing_trade_id_for_post_streak_probes",
        ):
            if "micro-validation" not in steps:
                steps.append("micro-validation")
            continue
        if b in ("governance_proof", "governance_trading_not_permitted"):
            steps.append("governance")
            continue
        if b == "supabase_proof":
            steps.append("supabase")
            continue
        if b == "reconciliation_proof":
            steps.append("reconciliation")
            continue
        if b == "ops_outputs_proof":
            steps.append("ops_outputs")
            continue
    return steps


def _manual_actions_now_block(
    chk: Dict[str, Any],
    schema: Dict[str, Any],
    gov: Dict[str, Any],
    streak: Dict[str, Any],
) -> List[str]:
    """Structured A/B/C manual actions for final_readiness_report.txt."""
    br = [str(x) for x in (chk.get("blocking_reasons") or [])]
    lines: List[str] = [
        "MANUAL ACTIONS REQUIRED NOW",
        "=========================",
        "",
        "A. Supabase",
        "-----------",
    ]
    supa_lines: List[str] = []
    if any("supabase" in x for x in br) or not schema.get("schema_ready"):
        hyp = str(schema.get("failure_hypothesis_operator") or "").strip()
        if hyp:
            supa_lines.append(f"  • {hyp}")
        supa_lines.append(
            "  • Run SQL from repo: supabase/ALL_REQUIRED_LIVE_MIGRATIONS.sql (or steps 1→2→3 per MIGRATION_ORDER.txt) "
            "in the Supabase SQL Editor for the project whose URL matches SUPABASE_URL."
        )
        supa_lines.append("  • Verify: SELECT * FROM public.trade_events LIMIT 1; (empty result OK).")
    else:
        supa_lines.append("  (none — schema_ready true; re-check if you change projects.)")
    lines.extend(supa_lines)
    lines.extend(["", "B. Governance", "-------------"])
    gov_lines: List[str] = []
    if any("governance" in x for x in br) or not gov.get("governance_trading_permitted"):
        gov_lines.append(
            "  • Ensure shark/memory/global/joint_review_latest.json exists and permits trading under enforcement, "
            "or rely on GOVERNANCE_JOINT_BOOTSTRAP=1 (default) to create a safe default once the path is writable."
        )
        gov_lines.append(
            "  • Read data/deployment/governance_manual_fix.txt after checklist for path + minimum JSON."
        )
    else:
        gov_lines.append("  (none — governance_trading_permitted true.)")
    lines.extend(gov_lines)
    lines.extend(["", "C. Live execution env", "----------------------"])
    env_lines: List[str] = []
    if any("validation_streak" in x or "COINBASE" in x or "LIVE_SINGLE" in x for x in br):
        env_lines.append("  • export COINBASE_EXECUTION_ENABLED=true")
        env_lines.append("  • export LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM=YES_I_UNDERSTAND_REAL_CAPITAL")
        env_lines.append("  • export NTE_EXECUTION_MODE=live")
        env_lines.append("  • export NTE_LIVE_TRADING_ENABLED=true")
        env_lines.append("  • unset EZRAS_DRY_RUN or set EZRAS_DRY_RUN=false")
        env_lines.append("  • See data/deployment/live_env_manual_fix.txt for full template.")
    else:
        env_lines.append("  (none required for streak — confirm NTE/Coinbase vars match your deployment.)")
    lines.extend(env_lines)
    lines.append("")
    return lines


def _manual_actions_required(
    chk: Dict[str, Any],
    schema: Dict[str, Any],
    gov: Dict[str, Any],
    streak: Dict[str, Any],
) -> List[str]:
    """Explicit human steps outside the repo (deduplicated)."""
    out: List[str] = []
    br = [str(x) for x in (chk.get("blocking_reasons") or [])]

    if any(x.startswith("supabase") or "supabase_schema" in x for x in br):
        out.append(
            "Supabase: In the project that matches SUPABASE_URL (and your key), apply SQL migrations from "
            "``supabase/MIGRATION_ORDER.txt`` in order (steps 1–3 minimum for ``trade_events``). "
            "Confirm Settings → API URL matches the env var and the key belongs to that project.",
        )
    if any("validation_streak" in x or "COINBASE" in x for x in br):
        out.append(
            "Live execution env: Set COINBASE_EXECUTION_ENABLED=true, "
            "LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM=YES_I_UNDERSTAND_REAL_CAPITAL, "
            "and keep EZRAS_DRY_RUN off — in your shell, ``.env``, or Railway/host variables.",
        )
    if any("governance_trading" in x or x.startswith("governance:") for x in br):
        out.append(
            "Governance: Provide a valid ``joint_review_latest.json`` (non-empty joint_review_id, "
            "integrity full, acceptable live_mode, not stale per policy) so governance_trading_permitted is true.",
        )
    if schema.get("issue_kind") == "manual_database_or_project_mismatch":
        out.append(
            "Schema probe: Remote error suggests missing tables/columns or wrong project — fix database state "
            "or URL/key alignment; see ``supabase_schema_readiness.json`` error_classification.",
        )
    if streak.get("streak_status") == "never_started_checklist_blocked":
        out.append(
            "Micro-validation: No live orders were sent — checklist was not green. "
            "Resolve checklist blockers, then re-run micro-validation.",
        )

    brs = str(streak.get("blocking_reason") or "")
    if "validation_product_policy_failure" in brs:
        out.append(
            "Micro-validation product policy: The streak failed because no validation product was both "
            "funded and allowed by your NTE ``products`` list (same list the live order guard uses). "
            "Typical fix: fund USD for BTC-USD, or add BTC-USDC to NTE products if you only hold USDC. "
            "Run: PYTHONPATH=src python3 scripts/validation_product_diagnostic.py",
        )

    if gov.get("governance_proof_ok") and not gov.get("governance_trading_permitted"):
        out.append(
            "Clarification: governance_proof_ok means gate logic is consistent; it does NOT mean trading is allowed. "
            "Check governance_trading_permitted and governance_trading_block_reason.",
        )

    seen: set[str] = set()
    deduped: List[str] = []
    for s in out:
        if s not in seen:
            seen.add(s)
            deduped.append(s)
    return deduped


def write_final_readiness_report(*, write_file: bool = True) -> str:
    """
    Plain-English answer: READY or NOT READY, why, and prioritized blockers.

    Writes ``data/deployment/final_readiness_report.txt``.
    """
    deployment_data_dir().mkdir(parents=True, exist_ok=True)
    fr = _read_json(final_readiness_path()) or {}
    chk = _read_json(checklist_json_path()) or {}
    streak = _read_json(streak_state_path()) or {}
    gov = _read_json(governance_proof_path()) or {}
    ops = _read_json(ops_outputs_proof_path()) or {}
    schema = _read_json(supabase_schema_readiness_path()) or {}

    ready_first = bool(fr.get("ready_for_first_20"))
    ready_micro = bool(fr.get("ready_for_live_micro_validation"))
    critical = list(fr.get("critical_blockers") or [])
    important = list(fr.get("important_blockers") or [])
    advisory = list(fr.get("advisory_notes") or [])
    reason = str(fr.get("reason") or "")

    status_word = "READY" if ready_first else "NOT READY"
    if ready_first:
        why = (
            "All gates passed: deployment checklist green, live micro-validation streak passed, "
            "governance + Supabase + reconciliation + ops outputs proofs clean, no trading halt, "
            "no unresolved partial failures."
        )
    else:
        why = (
            "One or more critical gates failed — do not start first-20. "
            "Fix items in priority order below, then re-run checklist → micro-validation → readiness."
        )
        if reason and reason != "not_ready":
            why += f" Summary code: {reason}."

    dsum = fr.get("deployable_capital_summary") or {}
    pvc = fr.get("policy_vs_capital_summary") or {}
    dvc = fr.get("direct_vs_convertible_summary") or {}
    lte = fr.get("live_truth_plain_english") or {}
    capital_note = ""
    if dsum or pvc or dvc:
        capital_note = (
            f"Deployable capital snapshot: conservative_quote≈{dsum.get('conservative_quote_usd_plus_usdc')}, "
            f"portfolio_mark_usd≈{dsum.get('portfolio_total_mark_value_usd')}. "
            f"Policy-vs-capital: {pvc}. Direct-vs-convertible: {dvc}."
        )
    live_truth_lines: List[str] = []
    if lte:
        live_truth_lines.extend(
            [
                "",
                "LIVE TRUTH (honest — from readiness JSON)",
                "==========================================",
                "What is code-ready but not yet live:",
            ]
        )
        for x in lte.get("what_is_code_ready_but_not_yet_live") or []:
            live_truth_lines.append(f"  - {x}")
        live_truth_lines.extend(["", "What is runtime-proven (when artifacts exist):", ""])
        for x in lte.get("what_is_runtime_proven") or []:
            live_truth_lines.append(f"  - {x}")
        live_truth_lines.extend(["", "What is advisory only (not order-enforced):", ""])
        for x in lte.get("what_is_advisory_only") or []:
            live_truth_lines.append(f"  - {x}")
        live_truth_lines.extend(["", "External deploy / scheduler still needed:", ""])
        for x in lte.get("what_needs_external_deploy_or_scheduler") or []:
            live_truth_lines.append(f"  - {x}")
        fb = lte.get("what_first_20_depends_on_that_is_still_unproven") or []
        if fb:
            live_truth_lines.extend(["", "First-20 still blocked by (critical list):", ""])
            for x in fb:
                live_truth_lines.append(f"  - {x}")
        ga = lte.get("gate_a_live_truth_summary")
        gb = lte.get("gate_b_live_truth_summary")
        if ga:
            live_truth_lines.extend(["", "Gate A classification:", json.dumps(ga, indent=2)[:4000]])
        if gb:
            live_truth_lines.extend(["", "Gate B summary:", json.dumps(gb, indent=2)[:4000]])

    failed_steps = _failed_runtime_steps(critical)
    steps_line = ", ".join(failed_steps) if failed_steps else "(n/a — READY or see JSON)"
    manual = _manual_actions_required(chk, schema, gov, streak)

    lines = [
        "READINESS LAYERS",
        "===============",
        "A) Code / repository — diagnostics, classification, and report text (this codebase).",
        "B) Runtime / environment — EZRAS_RUNTIME_ROOT, Supabase URL/key, Coinbase keys, Railway/shell env.",
        "C) Manual external — SQL migrations in your live Supabase project, joint review content, operator toggles.",
        "",
    ]
    lines.extend(_manual_actions_now_block(chk, schema, gov, streak))
    lines.append("ADDITIONAL MANUAL NOTES (detail)")
    lines.append("---------------------------------")
    if manual:
        for m in manual:
            lines.append(f"  • {m}")
    else:
        lines.append("  (none — or see checklist JSON for any remaining deployment blockers)")
    lines.extend(
        [
            "",
            "RERUN AFTER FIXES (from trading-ai directory)",
            "---------------------------------------------",
            "  cd trading-ai && PYTHONPATH=src python3 -m trading_ai.deployment checklist",
            "  PYTHONPATH=src python3 -m trading_ai.deployment micro-validation --n 3",
            "  PYTHONPATH=src python3 -m trading_ai.deployment readiness",
            "  PYTHONPATH=src python3 -m trading_ai.deployment final-report",
            "",
        ]
    )

    lines.extend(
        [
            "PLAIN-ENGLISH ANSWER",
            "===================",
            f"Overall status for first trades: {status_word}",
            "",
            f"Why: {why}",
            "",
        ]
    )
    if capital_note:
        lines.extend(
            [
                "Policy vs capital (latest artifacts)",
                "-----------------------------------",
                capital_note,
                "",
            ]
        )
    if live_truth_lines:
        lines.extend(live_truth_lines)
        lines.append("")
    lines.extend(
        [
            "If NOT READY — which runtime step failed (highest signal):",
            f"  {steps_line}",
            "",
            "Blockers in priority order",
            "-------------------------",
            "1) CRITICAL (must be empty to GO):",
        ],
    )
    if critical:
        for b in critical:
            lines.append(f"  - {b}")
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append("2) IMPORTANT (review before production stress):")
    if important:
        for b in important:
            lines.append(f"  - {b}")
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append("3) ADVISORY:")
    if advisory:
        for b in advisory:
            lines.append(f"  - {b}")
    else:
        lines.append("  (none)")
    lines.extend(
        [
            "",
            "Supporting facts:",
            f"  ready_for_live_micro_validation: {ready_micro}",
            f"  live_validation_streak_passed (streak file): {streak.get('live_validation_streak_passed')}",
            f"  streak_status (streak file): {streak.get('streak_status')}",
            f"  schema_ready (schema file): {schema.get('schema_ready')}",
            f"  governance_proof_ok (system consistent): {gov.get('governance_proof_ok')}",
            f"  governance_trading_permitted (orders allowed): {gov.get('governance_trading_permitted')}",
            f"  ops_outputs_ok: {ops.get('ops_outputs_ok')}",
            "",
            "EZRAS TRADING AI — FINAL LIVE READINESS (detail)",
            "=================================================",
            f"Generated: {iso_now()}",
            "",
            "--- FINAL READINESS (final_readiness.json) ---",
            json.dumps(fr, indent=2, default=str),
            "",
            "--- LATEST DEPLOYMENT CHECKLIST ---",
            json.dumps(
                {
                    "ready_for_live_micro_validation": chk.get("ready_for_live_micro_validation"),
                    "blocking_reasons": chk.get("blocking_reasons"),
                },
                indent=2,
                default=str,
            ),
            "",
            "--- LIVE MICRO-VALIDATION STREAK ---",
            json.dumps(
                {
                    "live_validation_streak_passed": streak.get("live_validation_streak_passed"),
                    "streak_status": streak.get("streak_status"),
                    "streak_interpretation": streak.get("streak_interpretation"),
                    "requested_notional_usd": streak.get("requested_notional_usd"),
                    "chosen_notional_usd": streak.get("chosen_notional_usd"),
                    "venue_min_notional_usd": streak.get("venue_min_notional_usd"),
                    "passed_run_count": streak.get("passed_run_count"),
                    "failed_run_count": streak.get("failed_run_count"),
                    "blocking_reason": streak.get("blocking_reason"),
                    "n_completed": streak.get("n_completed"),
                    "proof_references_by_run": streak.get("proof_references_by_run"),
                },
                indent=2,
                default=str,
            ),
            "",
            "--- SUPABASE SCHEMA READINESS ---",
            json.dumps(
                {
                    "schema_ready": schema.get("schema_ready"),
                    "combined_migration_file_repo": schema.get("combined_migration_file_repo"),
                    "failure_hypothesis_operator": schema.get("failure_hypothesis_operator"),
                    "supabase_url_runtime": schema.get("supabase_url_runtime"),
                    "required_migrations": schema.get("required_migrations"),
                    "missing_remote_objects": schema.get("missing_remote_objects"),
                    "blocking_reasons": schema.get("blocking_reasons"),
                },
                indent=2,
                default=str,
            ),
            "",
            "--- GOVERNANCE PROOF (latest file) ---",
            json.dumps(gov, indent=2, default=str)[:8000],
            "",
            "--- RECONCILIATION (last jsonl record) ---",
            _tail_jsonl(reconciliation_proof_jsonl_path(), 1),
            "",
            "--- SUPABASE PROOF (last jsonl record) ---",
            _tail_jsonl(supabase_proof_jsonl_path(), 1),
            "",
            "--- OPS OUTPUTS PROOF ---",
            json.dumps(ops, indent=2, default=str)[:6000],
            "",
        ]
    )
    text = "\n".join(lines)
    if write_file:
        outp = deployment_data_dir() / "final_readiness_report.txt"
        outp.write_text(text, encoding="utf-8")
    return text
