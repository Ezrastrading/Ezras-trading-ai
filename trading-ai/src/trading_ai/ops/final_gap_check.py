"""
Institutional final gap check — structured PASS/FAIL over operational subsystems.

``python -m trading_ai institutional final-gap-check``
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

from trading_ai.automation.risk_bucket import runtime_root


def _writable(p: Path) -> bool:
    try:
        p.mkdir(parents=True, exist_ok=True)
        t = p / ".write_probe"
        t.write_text("ok", encoding="utf-8")
        t.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def run_final_gap_check() -> Dict[str, Any]:
    failures: List[str] = []
    warnings: List[str] = []
    remaining: List[str] = []
    checked: Dict[str, Any] = {}

    root = runtime_root()
    state_dir = root / "state"
    logs_dir = root / "logs"
    if not _writable(state_dir):
        failures.append("state_dir_not_writable")
    if not _writable(logs_dir):
        failures.append("logs_dir_not_writable")
    checked["runtime_root"] = str(root)
    checked["state_writable"] = _writable(state_dir)
    checked["logs_writable"] = _writable(logs_dir)

    try:
        from trading_ai.automation.position_sizing_policy import sizing_status_snapshot

        ss = sizing_status_snapshot()
        checked["sizing_status"] = {"ok": True, "effective_preview": ss.get("effective_risk_preview")}
    except Exception as exc:
        failures.append(f"sizing_status:{exc}")
        checked["sizing_status"] = {"ok": False}

    try:
        from trading_ai.automation.risk_bucket import risk_state_path

        p = risk_state_path()
        checked["risk_bucket_state"] = {"exists": p.is_file(), "path": str(p)}
        if not p.is_file():
            warnings.append("risk_state_missing_will_default")
    except Exception as exc:
        failures.append(f"risk_bucket:{exc}")

    try:
        from trading_ai.automation.strategy_risk_bucket import strategy_risk_state_path

        p = strategy_risk_state_path()
        checked["strategy_risk_state"] = {"exists": p.is_file(), "path": str(p)}
    except Exception as exc:
        failures.append(f"strategy_risk:{exc}")

    try:
        from trading_ai.risk.hard_lockouts import get_effective_lockout

        hl = get_effective_lockout()
        checked["hard_lockouts"] = hl
        if hl.get("effective_lockout"):
            warnings.append("hard_lockout_active_not_a_failure")
    except Exception as exc:
        failures.append(f"hard_lockouts:{exc}")

    try:
        from trading_ai.execution.execution_reconciliation import get_execution_reconciliation_status

        checked["execution_reconciliation"] = get_execution_reconciliation_status()
    except Exception as exc:
        failures.append(f"execution_reconciliation:{exc}")

    try:
        from trading_ai.execution.venue_truth_sync import truth_sync_status

        checked["venue_truth"] = truth_sync_status()
    except Exception as exc:
        failures.append(f"venue_truth:{exc}")

    try:
        from trading_ai.ops.exception_dashboard import list_open_exceptions

        open_x = list_open_exceptions()
        checked["open_exceptions_count"] = len(open_x)
        crit = [e for e in open_x if str(e.get("severity")).upper() == "CRITICAL" and not e.get("resolved")]
        if crit:
            failures.append(f"unresolved_critical_exceptions:{len(crit)}")
        checked["unresolved_critical"] = len(crit)
    except Exception as exc:
        failures.append(f"exception_dashboard:{exc}")

    try:
        from trading_ai.governance import parameter_governance as pg

        drift = pg.check_tracked_parameter_drift(trigger="final_gap_check")
        checked["parameter_governance_drift"] = drift
        snap = pg.snapshot_tracked_parameters()
        checked["parameter_fingerprint"] = snap.get("fingerprint")
    except Exception as exc:
        failures.append(f"parameter_governance:{exc}")

    try:
        from trading_ai.reporting.daily_decision_memo import generate_daily_memo

        m = generate_daily_memo()
        checked["daily_memo"] = {"ok": bool(m.get("ok")), "path": m.get("path")}
        if not m.get("ok"):
            failures.append("daily_memo_generation_failed")
    except Exception as exc:
        failures.append(f"daily_memo:{exc}")

    try:
        from trading_ai.config import get_settings

        s = get_settings()
        tg = bool((os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()) and bool(
            (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
        )
        checked["telegram_env_configured"] = tg
        if getattr(s, "telegram_bot_token", None) and not tg:
            warnings.append("telegram_partial_env")
    except Exception as exc:
        warnings.append(f"settings_read:{exc}")

    checked["placeholder_scan"] = _scan_ambiguous_markers()

    ok = len(failures) == 0
    status = "PASS" if ok else "FAIL"
    return {
        "ok": ok,
        "status": status,
        "failures": failures,
        "warnings": warnings,
        "remaining_limitations": remaining,
        "checked_components": checked,
    }


def _scan_ambiguous_markers() -> Dict[str, Any]:
    """Lightweight static scan of institutional modules for banned vague tokens."""
    root_pkg = Path(__file__).resolve().parents[1]
    targets = [
        root_pkg / "execution" / "venue_truth_sync.py",
        root_pkg / "risk" / "hard_lockouts.py",
        root_pkg / "governance" / "parameter_governance.py",
        root_pkg / "institutional_cli.py",
    ]
    banned = ("not yet integrated", "placeholder logic", "skeleton only")
    hits: List[str] = []
    for t in targets:
        if not t.is_file():
            continue
        try:
            text = t.read_text(encoding="utf-8")
        except OSError:
            continue
        for b in banned:
            if b.lower() in text.lower():
                hits.append(f"{t.name}:{b}")
    return {"ambiguous_hits": hits, "clean": len(hits) == 0}
