"""
Deterministic daily operator memo from local runtime state (no LLM).

Memo: ``{EZRAS_RUNTIME_ROOT}/logs/daily_decision_memo.md``
State: ``{EZRAS_RUNTIME_ROOT}/state/daily_decision_memo_state.json``
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from trading_ai.automation.post_trade_hub import manifest_path, runtime_root
from trading_ai.automation.risk_bucket import risk_state_path


def memo_state_path() -> Path:
    return runtime_root() / "state" / "daily_decision_memo_state.json"


def memo_log_path() -> Path:
    return runtime_root() / "logs" / "daily_decision_memo.md"


def strategy_risk_path() -> Path:
    return runtime_root() / "state" / "strategy_risk_state.json"


def trade_quality_state_path() -> Path:
    return runtime_root() / "state" / "trade_quality_state.json"


def _read_json(p: Path) -> Dict[str, Any]:
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _derive_next_actions(
    lock: Dict[str, Any],
    ex: List[Dict[str, Any]],
    last_truth: Dict[str, Any],
    drift: Dict[str, Any],
) -> List[str]:
    actions: List[str] = []
    if lock.get("effective_lockout"):
        actions.append("Resolve active hard lockouts before increasing exposure.")
    if ex:
        actions.append(f"Clear or document {len(ex)} open exception(s).")
    v = str((last_truth or {}).get("last_verdict") or (last_truth or {}).get("verdict") or "")
    if v in ("MATERIAL_DRIFT", "ERROR"):
        actions.append("Investigate last venue truth sync verdict before new live orders.")
    if drift.get("drift_detected"):
        actions.append("Review automatic parameter drift record in governance log.")
    if not actions:
        actions.append("Run final-gap-check and review logs on schedule.")
    return actions[:5]


def generate_daily_memo(*, date_utc: str | None = None) -> Dict[str, Any]:
    day = date_utc or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rs = _read_json(risk_state_path())
    mf = _read_json(manifest_path())
    strat = _read_json(strategy_risk_path())
    tq = _read_json(trade_quality_state_path())
    try:
        from trading_ai.risk.hard_lockouts import get_effective_lockout

        lock = get_effective_lockout()
    except Exception:
        lock = {}
    try:
        from trading_ai.ops.exception_dashboard import list_open_exceptions

        ex = list_open_exceptions()
    except Exception:
        ex = []
    try:
        from trading_ai.execution.execution_reconciliation import get_execution_reconciliation_status

        erec = get_execution_reconciliation_status()
    except Exception:
        erec = {}
    try:
        from trading_ai.execution.venue_truth_sync import truth_sync_status

        vt = truth_sync_status()
    except Exception:
        vt = {}
    try:
        from trading_ai.governance import parameter_governance as pg

        drift = pg.check_tracked_parameter_drift(trigger="daily_memo")
        gov_snap = pg.snapshot_tracked_parameters()
    except Exception:
        drift = {}
        gov_snap = {}

    prev_lock = _read_json(memo_state_path()).get("last_lock_snapshot") or {}
    lock_delta = json.dumps(lock, sort_keys=True) != json.dumps(prev_lock, sort_keys=True)

    lines: List[str] = [
        f"# Daily operating memo — {day} UTC",
        "",
        "## 1. Activity summary",
        f"- Post-trade manifest: {json.dumps(mf.get('last_event'), default=str)}",
        f"- Risk equity_index: {rs.get('equity_index')} (peak {rs.get('peak_equity_index')})",
        f"- Recent closes (win/loss window): {rs.get('recent_results')}",
        "",
        "## 2. Risk state",
        json.dumps(
            {
                "equity_index": rs.get("equity_index"),
                "peak_equity_index": rs.get("peak_equity_index"),
                "recent_results": rs.get("recent_results"),
            },
            indent=2,
        ),
        "",
        "## 3. Hard lockouts",
        json.dumps(lock, indent=2),
        f"- Lockout state changed since last memo: {lock_delta}",
        "",
        "## 4. Execution reconciliation (aggregate)",
        json.dumps(
            {
                "trade_count": erec.get("trade_count"),
                "last_reconciliations": (erec.get("last_reconciliations") or [])[-5:],
            },
            indent=2,
            default=str,
        ),
        "",
        "## 5. Venue truth sync",
        json.dumps(vt, indent=2, default=str),
        "",
        "## 6. Strategy risk (per-strategy windows)",
        json.dumps({"strategies": list((strat.get("strategies") or {}).keys())[:32]}, indent=2),
        "",
        "## 7. Trade quality (recent scores)",
        json.dumps((tq.get("scores") or [])[-8:], indent=2, default=str),
        "",
        "## 8. Open exceptions",
        json.dumps(
            [{"id": x.get("id"), "category": x.get("category"), "severity": x.get("severity")} for x in ex[:24]],
            indent=2,
        ),
        "",
        "## 9. Parameter governance",
        f"- Fingerprint: {gov_snap.get('fingerprint')}",
        json.dumps(drift, indent=2, default=str),
        "",
        "## 10. Deterministic next actions",
    ]
    for i, a in enumerate(_derive_next_actions(lock, ex, vt, drift), 1):
        lines.append(f"{i}. {a}")

    body = "\n".join(lines) + "\n"
    memo_log_path().parent.mkdir(parents=True, exist_ok=True)
    memo_log_path().write_text(body, encoding="utf-8")
    st = {
        "version": 2,
        "last_generated": datetime.now(timezone.utc).isoformat(),
        "day": day,
        "last_lock_snapshot": lock,
    }
    memo_state_path().parent.mkdir(parents=True, exist_ok=True)
    memo_state_path().write_text(json.dumps(st, indent=2), encoding="utf-8")
    return {"ok": True, "path": str(memo_log_path()), "open_exceptions_count": len(ex), "day": day}


def show_last_memo() -> Dict[str, Any]:
    p = memo_log_path()
    if not p.is_file():
        return {"ok": False, "error": "no_memo"}
    return {"ok": True, "content": p.read_text(encoding="utf-8")[:32000]}
