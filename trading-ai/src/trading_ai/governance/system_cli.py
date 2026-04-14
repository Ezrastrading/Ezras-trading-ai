"""CLI entrypoints: consistency, storage map, automation scope, integrity."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any, List, Optional

from trading_ai.config import get_settings
from trading_ai.governance.consistency_engine import (
    ConsistencyBaseline,
    evaluate_doctrine_alignment,
    get_consistency_status,
    get_full_integrity_report,
    load_baseline,
    save_baseline,
)
from trading_ai.governance.operator_registry import (
    approve_doctrine,
    register_operator,
    registry_status,
)
from trading_ai.governance.system_doctrine import compute_doctrine_sha256
from trading_ai.governance.temporal_consistency import build_temporal_summary
from trading_ai.ops.automation_scope import build_automation_scope_snapshot
from trading_ai.ops.storage_architecture import build_storage_snapshot


def _print_json(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str))


def main_consistency(argv: Optional[List[str]] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(prog="trading-ai consistency")
    sub = p.add_subparsers(dest="action", required=True)

    sub.add_parser("status", help="Full governance status (registry, chain, temporal, heartbeat)")
    sub.add_parser("check-sample", help="Sample aligned + conflicting evaluations (writes audit chain)")
    sub.add_parser("show-operator-registry", help="Operator + doctrine approval registry")

    p_reg = sub.add_parser("register-operator", help="Register an operator id")
    p_reg.add_argument("--id", required=True, dest="operator_id")
    p_reg.add_argument("--role", default="owner")
    p_reg.add_argument("--signing-key-id", default="", dest="signing_key_id")

    p_app = sub.add_parser("approve-doctrine", help="Approve current canonical doctrine hash")
    p_app.add_argument("--operator-id", required=True, dest="operator_id")
    p_app.add_argument("--version", required=True, dest="doctrine_version")
    p_app.add_argument("--notes", default="")

    p_base = sub.add_parser("baseline", help="Save current doctrine hash as baseline")
    p_base.add_argument("--label", default="operator_baseline", help="Baseline label")
    p_base.add_argument("--notes", default="", help="Freeform notes")

    sub.add_parser("diff", help="Compare current doctrine hash to saved baseline")
    sub.add_parser("temporal", help="Temporal consistency windows + trend classification")

    sub.add_parser(
        "activate-local-operator",
        help="Register victor_local_primary + approve doctrine (local activation)",
    )

    args = p.parse_args(list(argv))

    if args.action == "status":
        _print_json(get_consistency_status())
        return 0

    if args.action == "check-sample":
        good = evaluate_doctrine_alignment(
            change_type="governance_change",
            payload={"summary": "tighten review cadence", "operator_approved": True},
        )
        bad = evaluate_doctrine_alignment(
            change_type="strategy_change",
            payload={"notes": "ignore risk limits for speed"},
        )
        _print_json(
            {
                "aligned_sample": good.to_dict(),
                "conflicting_sample": bad.to_dict(),
            }
        )
        return 0

    if args.action == "show-operator-registry":
        _print_json(registry_status())
        return 0

    if args.action == "register-operator":
        _print_json(
            register_operator(
                operator_id=args.operator_id,
                role=args.role,
                signing_key_id=args.signing_key_id or "",
            )
        )
        return 0

    if args.action == "approve-doctrine":
        _print_json(
            approve_doctrine(
                operator_id=args.operator_id,
                doctrine_version=args.doctrine_version,
                notes=args.notes,
            )
        )
        return 0

    if args.action == "baseline":
        bl = ConsistencyBaseline(
            label=args.label,
            notes=args.notes,
            created_at=datetime.now(timezone.utc),
            doctrine_sha256=compute_doctrine_sha256(),
        )
        save_baseline(bl)
        _print_json({"ok": True, "baseline": bl.to_dict()})
        return 0

    if args.action == "diff":
        cur = compute_doctrine_sha256()
        base = load_baseline()
        _print_json(
            {
                "current_sha256": cur,
                "baseline": base.to_dict() if base else None,
                "match": base.doctrine_sha256 == cur if base else None,
            }
        )
        return 0

    if args.action == "temporal":
        _print_json(build_temporal_summary())
        return 0

    if args.action == "activate-local-operator":
        from trading_ai.ops.activation_control import activate_local_operator

        _print_json(activate_local_operator())
        return 0

    return 1


def main_storage(argv: Optional[List[str]] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(prog="trading-ai storage")
    sub = p.add_subparsers(dest="action", required=True)
    sub.add_parser("status", help="Storage architecture snapshot")
    args = p.parse_args(list(argv))
    if args.action == "status":
        settings = get_settings()
        _print_json(build_storage_snapshot(settings=settings))
        return 0
    return 1


def main_automation_scope(argv: Optional[List[str]] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(prog="trading-ai automation-scope")
    sub = p.add_subparsers(dest="action", required=True)
    sub.add_parser("status", help="Automation map + heartbeat health")
    args = p.parse_args(list(argv))
    if args.action == "status":
        _print_json(build_automation_scope_snapshot())
        return 0
    return 1


def main_integrity_check(argv: Optional[List[str]] = None) -> int:
    from trading_ai.governance.audit_chain import append_chained_event, verify_audit_chain

    rep = get_full_integrity_report()
    st = get_consistency_status()
    if not rep.get("overall_ok") and verify_audit_chain().ok:
        try:
            append_chained_event({"kind": "integrity_failure_snapshot", "full_integrity": rep})
        except OSError:
            pass
    _print_json({"full_integrity": rep, "consistency_status": st})
    return 0 if rep.get("overall_ok") else 2
