"""CLI: print adaptive proof file status (existence, size, mtime, parsed summary)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from trading_ai.control.adaptive_proof_validation import (
    validate_adaptive_live_proof_file,
    validate_adaptive_routing_proof_file,
)
from trading_ai.control.adaptive_routing_live import adaptive_routing_proof_path
from trading_ai.control.live_adaptive_integration import adaptive_live_proof_path


def _fmt_path(p: Path) -> str:
    try:
        return str(p.resolve())
    except Exception:
        return str(p)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Verify adaptive_live_proof.json and adaptive_routing_proof.json under EZRAS_RUNTIME_ROOT.",
    )
    p.add_argument(
        "--max-age-hours",
        type=float,
        default=168.0,
        help="Max age for generated_at before stale warning (default 168h).",
    )
    args = p.parse_args(argv)
    max_age_sec = float(args.max_age_hours) * 3600.0

    live = adaptive_live_proof_path()
    route = adaptive_routing_proof_path()

    vl = validate_adaptive_live_proof_file(live, max_age_sec=max_age_sec)
    vr = validate_adaptive_routing_proof_file(route, max_age_sec=max_age_sec)

    summary = {
        "adaptive_live_proof": {
            "path": _fmt_path(live),
            "validation": vl,
            "parsed_summary": _live_summary(live) if live.is_file() else {},
        },
        "adaptive_routing_proof": {
            "path": _fmt_path(route),
            "validation": vr,
            "parsed_summary": _route_summary(route) if route.is_file() else {},
        },
        "both_ok": bool(vl.get("ok")) and bool(vr.get("ok")),
    }
    print(json.dumps(summary, indent=2, default=str))
    return 0 if summary["both_ok"] else 2


def _live_summary(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        return {
            "generated_at": raw.get("generated_at"),
            "proof_source": raw.get("proof_source"),
            "entrypoint": raw.get("entrypoint"),
            "route": raw.get("route"),
            "venue": raw.get("venue"),
            "gate": raw.get("gate"),
            "current_operating_mode": raw.get("current_operating_mode") or raw.get("mode"),
            "allow_new_trades": raw.get("allow_new_trades"),
            "size_multiplier": raw.get("size_multiplier"),
            "product_id": raw.get("product_id"),
        }
    except Exception:
        return {}


def _route_summary(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        return {
            "generated_at": raw.get("generated_at"),
            "proof_source": raw.get("proof_source"),
            "entrypoint": raw.get("entrypoint"),
            "allocation_source": raw.get("allocation_source") or raw.get("route_source"),
            "recommended_gate_allocations": raw.get("recommended_gate_allocations"),
            "fallback_reason": raw.get("fallback_reason"),
        }
    except Exception:
        return {}


if __name__ == "__main__":
    sys.exit(main())
