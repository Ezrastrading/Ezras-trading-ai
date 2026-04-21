"""Daily / weekly self-learning reviews — proposals are never auto-executed."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.learning.authority_model import weekly_proposal_envelope
from trading_ai.learning.self_learning_memory import refresh_last_48h_mastery, write_system_mastery_report
from trading_ai.runtime_paths import ezras_runtime_root


def _read_recent_log_lines(path: Path, max_lines: int = 400) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: List[Dict[str, Any]] = []
    for ln in lines[-max_lines:]:
        ln = ln.strip()
        if not ln:
            continue
        try:
            o = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if isinstance(o, dict):
            out.append(o)
    return out


def _patterns_mistakes(rows: List[Dict[str, Any]]) -> tuple[List[str], List[str]]:
    mistakes: List[str] = []
    patterns: List[str] = []
    for r in rows[-80:]:
        et = str(r.get("event_type") or "")
        if et in ("failure", "blocked_trade"):
            mistakes.append(str(r.get("what_happened") or "")[:240])
        if r.get("requires_ceo_review"):
            patterns.append(f"CEO review suggested: {et} — {r.get('why_it_happened')}")
    return patterns, mistakes


def run_daily_learning_if_needed(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    today = datetime.now(timezone.utc).date().isoformat()
    marker = root / "data" / "learning" / "last_daily_learning_date.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    last = ""
    if marker.is_file():
        last = marker.read_text(encoding="utf-8").strip()
    if last == today:
        return {"status": "skipped", "reason": "already_ran_today", "date": today}

    log = root / "data" / "learning" / "system_learning_log.jsonl"
    rows = _read_recent_log_lines(log)
    patterns, mistakes = _patterns_mistakes(rows)
    open_q = [
        "Are repeated blocks due to policy vs funding vs venue min?",
        "Is win rate statistically meaningful yet or still noise?",
    ]
    pat_lines = [f"  - {p}" for p in patterns[:12]] or ["  - (none recent)"]
    mis_lines = [f"  - {m}" for m in mistakes[:12]] or ["  - (none recent)"]
    body_txt = "\n".join(
        [
            f"daily_self_learning_review date={today}",
            "",
            "patterns_observed:",
            *pat_lines,
            "",
            "mistakes / failures:",
            *mis_lines,
            "",
            "improvements (advisory):",
            "  - Tighten measurement on blocked_trade root causes.",
            "  - Keep ratio changes as proposals until operator approves.",
            "",
            "open_questions:",
            *[f"  - {q}" for q in open_q],
        ]
    )
    sess = root / "data" / "review" / "ai_self_learning_sessions"
    sess.mkdir(parents=True, exist_ok=True)
    (sess / "daily_self_learning_review.txt").write_text(body_txt, encoding="utf-8")

    review_json = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "summary_date": today,
        "patterns_observed": patterns[:20],
        "mistakes": mistakes[:20],
        "improvements_suggested": [
            "Review CEO flags before scaling",
            "Keep Gate A / Gate B scopes separate in dashboards",
        ],
        "open_questions": open_q,
        "status": "proposal_only_not_executed",
    }
    rv = root / "data" / "review"
    rv.mkdir(parents=True, exist_ok=True)
    (rv / "daily_ai_self_learning_review.json").write_text(
        json.dumps(review_json, indent=2, default=str),
        encoding="utf-8",
    )
    (rv / "daily_ai_self_learning_review.txt").write_text(body_txt, encoding="utf-8")

    marker.write_text(today, encoding="utf-8")
    refresh_last_48h_mastery(runtime_root=root)
    write_system_mastery_report(runtime_root=root)

    wk = _maybe_weekly_synthesis(runtime_root=root, rows=rows)
    return {"status": "ok", "date": today, "weekly": wk}


def _maybe_weekly_synthesis(*, runtime_root: Path, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    meta_p = runtime_root / "data" / "learning" / "weekly_learning_meta.json"
    iso_year, iso_week, _ = datetime.now(timezone.utc).isocalendar()
    key = f"{iso_year}-W{iso_week:02d}"
    prev = {}
    if meta_p.is_file():
        try:
            prev = json.loads(meta_p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            prev = {}
    if isinstance(prev, dict) and prev.get("last_week_key") == key:
        return {"status": "skipped", "week": key}

    proposals = weekly_proposal_envelope(
        [
            {
                "kind": "strategy_delta",
                "detail": "Review edge registry vs recent regime tags (proposal only).",
                "status": "proposal_only_not_executed",
            },
            {
                "kind": "ratio_delta",
                "detail": "If drawdown elevated, consider soft reserve bump — operator approval required.",
                "status": "proposal_only_not_executed",
            },
            {
                "kind": "risk_delta",
                "detail": "If blocked_trade cluster on same root cause, tighten preflight messaging only.",
                "status": "proposal_only_not_executed",
            },
        ]
    )
    out_path = runtime_root / "data" / "review" / "weekly_learning_synthesis.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(proposals, indent=2, default=str), encoding="utf-8")
    meta_p.parent.mkdir(parents=True, exist_ok=True)
    meta_p.write_text(
        json.dumps({"last_week_key": key, "updated_at_utc": datetime.now(timezone.utc).isoformat()}),
        encoding="utf-8",
    )
    return {"status": "ok", "week": key, "path": str(out_path)}
