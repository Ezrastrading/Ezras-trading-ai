"""Morning / evening vault-style summaries for Telegram (truth + inbox + partner queue)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from trading_ai.automation.telegram_ops import monorepo_root
from trading_ai.truth.paths_truth import uncertainty_registry_path


def _vault_inbox_dir() -> Path:
    env = (os.environ.get("EZRAS_VAULT_INBOX") or "").strip()
    if env:
        return Path(env).expanduser()
    return monorepo_root() / "vault_inbox"


def _count_inbox_files() -> int:
    d = _vault_inbox_dir()
    if not d.is_dir():
        return 0
    n = 0
    for p in d.iterdir():
        if p.is_file() and not p.name.startswith("."):
            n += 1
    return n


def _uncertainty_unresolved_count() -> int:
    p = uncertainty_registry_path()
    if not p.is_file():
        return 0
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    items = data.get("items") if isinstance(data, dict) else []
    if not isinstance(items, list):
        return 0
    n = 0
    for it in items:
        if not isinstance(it, dict):
            n += 1
            continue
        st = str(it.get("status") or "open").lower()
        if st in ("resolved", "closed", "dismissed"):
            continue
        n += 1
    return n


def _validation_failure_count() -> int:
    try:
        from trading_ai.truth.final_truth_gap_check import run_final_truth_gap_check

        r = run_final_truth_gap_check()
        return int(r.get("gap_count") or len(r.get("remaining_gaps") or []))
    except Exception:
        return -1


def _top_actions(limit: int = 3) -> List[str]:
    try:
        from trading_ai.business_ops.partner_loop.partner_action_queue import list_actions

        rows = list_actions()
    except Exception:
        return []
    out: List[str] = []
    for r in reversed(rows):
        if not isinstance(r, dict):
            continue
        t = str(r.get("title") or "").strip()
        if t:
            out.append(t)
        if len(out) >= limit:
            break
    return out[:limit]


def _readiness(gap_count: int, bootstrap_ok: bool) -> str:
    if not bootstrap_ok:
        return "BLOCKED (truth bootstrap)"
    if gap_count < 0:
        return "UNKNOWN (gap check failed)"
    if gap_count == 0:
        return "READY"
    return "ATTENTION_REQUIRED"


def build_morning_vault_summary() -> Dict[str, Any]:
    from trading_ai.truth.bootstrap import validate_truth_layer_bootstrap

    boot = validate_truth_layer_bootstrap()
    pending_raw = _count_inbox_files()
    val_failures = _validation_failure_count()
    unknown_n = _uncertainty_unresolved_count()
    actions = _top_actions(3)
    readiness = _readiness(val_failures, bool(boot.get("ok")))

    return {
        "kind": "morning",
        "pending_raw_files": pending_raw,
        "validation_failures": val_failures,
        "unresolved_unknown_count": unknown_n,
        "top_action_items": actions,
        "vault_readiness": readiness,
        "vault_inbox_dir": str(_vault_inbox_dir()),
        "truth_bootstrap_ok": bool(boot.get("ok")),
    }


def _state_path() -> Path:
    return monorepo_root() / "logs" / "vault_cycle_state.json"


def record_intake_failure(note: str = "") -> None:
    """Append one failure line for today's evening summary (optional intake automation)."""
    p = monorepo_root() / "logs" / "vault_intake_failures.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": datetime.now(timezone.utc).isoformat(), "note": note}
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def _failures_today_count() -> int:
    p = monorepo_root() / "logs" / "vault_intake_failures.jsonl"
    if not p.is_file():
        return 0
    day = datetime.now(timezone.utc).date().isoformat()
    n = 0
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = str(row.get("ts") or "")
            if ts.startswith(day):
                n += 1
    except OSError:
        return 0
    return n


def _top_findings(limit: int = 3) -> List[str]:
    try:
        from trading_ai.business_ops.partner_loop.partner_findings_tracker import list_recent_findings

        rows = list_recent_findings()
    except Exception:
        return []
    out: List[str] = []
    for r in reversed(rows[-50:]):
        if not isinstance(r, dict):
            continue
        t = str(r.get("text") or "").strip()
        if t:
            out.append(t[:240] + ("…" if len(t) > 240 else ""))
        if len(out) >= limit:
            break
    return out[:limit]


def _top_unresolved_unknowns(limit: int = 3) -> List[str]:
    p = uncertainty_registry_path()
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = data.get("items") if isinstance(data, dict) else []
    if not isinstance(items, list):
        return []
    out: List[str] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        st = str(it.get("status") or "open").lower()
        if st in ("resolved", "closed", "dismissed"):
            continue
        label = str(it.get("label") or it.get("id") or it.get("text") or "")[:200]
        if label:
            out.append(label)
        if len(out) >= limit:
            break
    return out


def _load_cycle_state() -> Dict[str, Any]:
    p = _state_path()
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cycle_state(data: Dict[str, Any]) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(p)


def record_morning_snapshot() -> Dict[str, Any]:
    """Persist inbox count after morning run (for evening delta)."""
    st = _load_cycle_state()
    st["last_morning"] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "inbox_count": _count_inbox_files(),
        "inbox_dir": str(_vault_inbox_dir()),
    }
    _save_cycle_state(st)
    return st["last_morning"]


def build_evening_vault_summary() -> Dict[str, Any]:
    st = _load_cycle_state()
    morning = st.get("last_morning") or {}
    start_ct = int(morning.get("inbox_count") or 0)
    cur = _count_inbox_files()
    processed = max(0, start_ct - cur)
    failed = _failures_today_count()

    return {
        "kind": "evening",
        "files_processed": processed,
        "files_failed": failed,
        "top_insights": _top_findings(3),
        "top_unresolved_unknowns": _top_unresolved_unknowns(3),
        "next_actions": _top_actions(3),
        "inbox_start_morning": start_ct,
        "inbox_now": cur,
    }


def format_morning_telegram(summary: Dict[str, Any]) -> str:
    lines = [
        "Ezras — MORNING VAULT CYCLE",
        f"pending_raw_files: {summary.get('pending_raw_files')}",
        f"validation_failures: {summary.get('validation_failures')}",
        f"unresolved_unknown_count: {summary.get('unresolved_unknown_count')}",
        f"vault_readiness: {summary.get('vault_readiness')}",
        f"inbox_dir: {summary.get('vault_inbox_dir')}",
    ]
    acts = summary.get("top_action_items") or []
    if acts:
        lines.append("top_actions:")
        for i, a in enumerate(acts, 1):
            lines.append(f"  {i}. {a}")
    else:
        lines.append("top_actions: (none)")
    return "\n".join(lines)


def format_evening_telegram(summary: Dict[str, Any]) -> str:
    lines = [
        "Ezras — EVENING VAULT CYCLE",
        f"files_processed (inbox cleared since morning): {summary.get('files_processed')}",
        f"files_failed (intake log today): {summary.get('files_failed')}",
    ]
    ins = summary.get("top_insights") or []
    if ins:
        lines.append("top_insights:")
        for i, t in enumerate(ins, 1):
            lines.append(f"  {i}. {t}")
    unk = summary.get("top_unresolved_unknowns") or []
    if unk:
        lines.append("top_unresolved_unknowns:")
        for i, t in enumerate(unk, 1):
            lines.append(f"  {i}. {t}")
    na = summary.get("next_actions") or []
    if na:
        lines.append("next_actions:")
        for i, t in enumerate(na, 1):
            lines.append(f"  {i}. {t}")
    return "\n".join(lines)
