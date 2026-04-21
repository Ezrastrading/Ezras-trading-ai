"""CLI: ``python -m trading_ai.ratios <cmd>`` — ratio artifacts and audits."""

from __future__ import annotations

import argparse
import json
import sys

from trading_ai.runtime_paths import ezras_runtime_root


def main() -> int:
    p = argparse.ArgumentParser(prog="python -m trading_ai.ratios")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("write-all", help="ratio_policy_snapshot + reserve + optional deployable txt refresh")
    sub.add_parser("daily-review", help="daily_ratio_review.json under data/review")
    sub.add_parser("mastery", help="last_48h_system_mastery + ratio_memory under data/learning")
    sub.add_parser("activation-audit", help="recent_work_activation_audit under data/control")
    sub.add_parser("integration-audit", help="integration_structural_audit under data/learning and control")
    sub.add_parser(
        "gap-closure",
        help="honest_live_status_matrix + final_gap_closure_audit under data/control",
    )
    sub.add_parser("write-everything", help="all of the above in one run")

    args = p.parse_args()
    root = ezras_runtime_root()

    if args.cmd == "write-all":
        from trading_ai.ratios.artifacts_writer import write_all_ratio_artifacts

        out = write_all_ratio_artifacts(runtime_root=root)
        print(json.dumps(out, indent=2))
        return 0
    if args.cmd == "daily-review":
        from trading_ai.ratios.daily_ratio_review import write_daily_ratio_review

        print(json.dumps(write_daily_ratio_review(root), indent=2))
        return 0
    if args.cmd == "mastery":
        from trading_ai.ratios.system_mastery import write_last_48h_system_mastery

        print(json.dumps(write_last_48h_system_mastery(root), indent=2))
        return 0
    if args.cmd == "activation-audit":
        from trading_ai.ratios.recent_work_activation import write_recent_work_activation_audit

        print(json.dumps(write_recent_work_activation_audit(root), indent=2))
        return 0
    if args.cmd == "integration-audit":
        from trading_ai.ratios.integration_structural_audit import write_integration_audit_artifacts

        print(json.dumps(write_integration_audit_artifacts(root), indent=2))
        return 0
    if args.cmd == "gap-closure":
        from trading_ai.ratios.gap_closure import write_honest_gap_artifacts

        print(json.dumps(write_honest_gap_artifacts(runtime_root=root), indent=2, default=str))
        return 0
    if args.cmd == "write-everything":
        from trading_ai.multi_avenue.writer import write_multi_avenue_control_bundle
        from trading_ai.ratios.artifacts_writer import write_all_ratio_artifacts
        from trading_ai.ratios.daily_ratio_review import write_daily_ratio_review
        from trading_ai.ratios.gap_closure import write_honest_gap_artifacts
        from trading_ai.ratios.integration_structural_audit import write_integration_audit_artifacts
        from trading_ai.ratios.recent_work_activation import write_recent_work_activation_audit
        from trading_ai.ratios.system_mastery import write_last_48h_system_mastery

        agg: dict = {}
        agg["ratio"] = write_all_ratio_artifacts(runtime_root=root)
        agg["daily"] = write_daily_ratio_review(root)
        agg["mastery"] = write_last_48h_system_mastery(root)
        agg["activation"] = write_recent_work_activation_audit(root)
        agg["integration"] = write_integration_audit_artifacts(root)
        agg["honest_gap"] = write_honest_gap_artifacts(runtime_root=root)
        agg["multi_avenue"] = write_multi_avenue_control_bundle(runtime_root=root)
        print(json.dumps(agg, indent=2, default=str))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
