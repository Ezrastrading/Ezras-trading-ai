"""Daily master operator report — federated trades + Gate A/B + control snapshots (human-readable)."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.reports.daily_trading_summary import build_daily_trading_summary
from trading_ai.shark.coinbase_spot.gate_b_live_status import gate_b_live_status_report


def _ticket_tail(runtime_root: Path, limit: int = 20) -> List[Dict[str, Any]]:
    p = runtime_root / "data" / "tickets" / "tickets.jsonl"
    if not p.is_file():
        return []
    out: List[Dict[str, Any]] = []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()[-limit:]
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return out


def build_daily_master_operator_report(
    *,
    runtime_root: Optional[Path] = None,
    day: Optional[date] = None,
) -> Dict[str, Any]:
    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    day = day or datetime.now(timezone.utc).date()

    dsum = build_daily_trading_summary(runtime_root=root, day=day)
    gb = gate_b_live_status_report()

    tickets = _ticket_tail(root)
    gate_b_tickets = [t for t in tickets if str(t.get("gate_id") or "").lower() == "gate_b"]
    gate_a_tickets = [t for t in tickets if str(t.get("gate_id") or "").lower() in ("gate_a", "")]

    sections = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary_date": day.isoformat(),
            "runtime_root": str(root),
        },
        "gate_a_block": dsum.get("per_avenue", {}).get("Avenue A", {}).get("Gate A"),
        "gate_b_block": {
            "trades_today": dsum.get("per_avenue", {})
            .get("Avenue A", {})
            .get("Gate B", {}),
            "live_status": gb.get("production_state"),
            "readiness_state": gb.get("readiness_state"),
            "gate_b_ready_for_live": gb.get("gate_b_ready_for_live"),
        },
        "totals": dsum.get("system_summary"),
        "tickets": {
            "last_n_all": len(tickets),
            "gate_b_recent": len(gate_b_tickets),
            "gate_a_recent": len(gate_a_tickets),
        },
        "federation": dsum.get("federation_meta"),
    }

    txt_lines = [
        f"DAILY MASTER OPERATOR REPORT — {day.isoformat()}",
        f"runtime_root: {root}",
        "",
        "=== GATE A (Coinbase / NTE-style) ===",
        json.dumps(sections["gate_a_block"], indent=2, default=str),
        "",
        "=== GATE B (Kalshi / gainers path) ===",
        json.dumps(sections["gate_b_block"], indent=2, default=str),
        "",
        "=== SYSTEM TOTALS ===",
        json.dumps(sections["totals"], indent=2, default=str),
        "",
        "=== TICKET SNAPSHOT (tail) ===",
        f"recent lines scanned: {len(tickets)}; gate_b tagged in tail: {len(gate_b_tickets)}",
        "",
        dsum.get("plain_english", ""),
    ]
    sections["plain_english"] = "\n".join(txt_lines)
    return sections


def write_daily_master_operator_report(
    *,
    runtime_root: Optional[Path] = None,
    day: Optional[date] = None,
) -> Dict[str, Any]:
    from trading_ai.storage.storage_adapter import LocalStorageAdapter

    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    adapter = LocalStorageAdapter(runtime_root=root)
    payload = build_daily_master_operator_report(runtime_root=root, day=day)
    adapter.write_text("data/reports/daily_master_operator_report.txt", payload.get("plain_english") or "")
    adapter.write_json("data/reports/daily_master_operator_report.json", payload)
    return payload
