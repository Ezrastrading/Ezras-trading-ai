"""
First 60-day live operations calendar — seed artifacts, day index, review envelopes.

Reads ``data/control/first_60_day_state.json`` for ``live_start_date_iso`` (UTC date).
If unset, live-ops attachments are skipped (no fake day index).
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from trading_ai.control.paths import control_data_dir, repo_packaged_control_defaults_dir
from trading_ai.review.paths import review_data_dir
from trading_ai.runtime_paths import ezras_runtime_root

_CONTROL_NAMES = (
    "first_60_day_calendar.json",
    "scaling_gates.json",
    "halt_rules.json",
    "daily_ceo_review_template.json",
    "weekly_ceo_review_template.json",
    "live_dashboard_thresholds.json",
    "first_60_day_state.json",
)


def ensure_first_60_day_control_artifacts(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """Copy packaged defaults into runtime ``data/control`` when files are missing."""
    cdir = control_data_dir(runtime_root)
    src_root = repo_packaged_control_defaults_dir()
    copied: List[str] = []
    skipped: List[str] = []
    for name in _CONTROL_NAMES:
        dest = cdir / name
        if dest.is_file():
            skipped.append(name)
            continue
        packaged = src_root / name
        if packaged.is_file():
            shutil.copy2(packaged, dest)
            copied.append(name)
        else:
            skipped.append(f"{name}(missing_packaged_default)")
    return {"control_dir": str(cdir), "copied": copied, "skipped": skipped}


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _state_path(runtime_root: Optional[Path]) -> Path:
    return control_data_dir(runtime_root) / "first_60_day_state.json"


def live_calendar_day_number(*, runtime_root: Optional[Path] = None, as_of: Optional[date] = None) -> Optional[int]:
    """
    1-based day since configured live start (unbounded), or None if start not configured / pre-start.

    Honors env override ``EZRAS_LIVE_START_DATE`` (YYYY-MM-DD) for tests.
    """
    as_of = as_of or _utc_today()
    env_start = (os.environ.get("EZRAS_LIVE_START_DATE") or "").strip()
    start: Optional[date] = None
    if env_start:
        try:
            start = date.fromisoformat(env_start[:10])
        except ValueError:
            start = None
    if start is None:
        st = _load_json(_state_path(runtime_root))
        raw = str((st or {}).get("live_start_date_iso") or "").strip()
        if raw:
            try:
                start = date.fromisoformat(raw[:10])
            except ValueError:
                start = None
    if start is None:
        return None
    n = (as_of - start).days + 1
    if n < 1:
        return None
    return n


def _day_entry(days: List[Mapping[str, Any]], n: int) -> Optional[Dict[str, Any]]:
    for d in days:
        if int(d.get("day") or 0) == n:
            return dict(d)
    return None


def _week_block_for_day(weeks: List[Mapping[str, Any]], day_n: int) -> Optional[Dict[str, Any]]:
    for w in weeks:
        lo = int(w.get("day_range_start") or 0)
        hi = int(w.get("day_range_end") or 0)
        if lo <= day_n <= hi:
            return dict(w)
    return dict(weeks[-1]) if weeks and day_n > int(weeks[-1].get("day_range_end") or 0) else None


def attach_first_60_context_for_ceo_review(
    diagnosis: Mapping[str, Any],
    *,
    runtime_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Structured slice for ``ceo_daily_review`` — safe when calendar not active."""
    ensure_first_60_day_control_artifacts(runtime_root=runtime_root)
    cdir = control_data_dir(runtime_root)
    cal = _load_json(cdir / "first_60_day_calendar.json")
    day_n = live_calendar_day_number(runtime_root=runtime_root)
    if day_n is None or not cal:
        return {"active": False, "reason": "live_start_not_configured_or_calendar_missing"}
    day_for_blocks = min(day_n, 60)
    post_window = day_n > 60
    days = [x for x in (cal.get("days_1_14") or []) if isinstance(x, dict)]
    weeks = [x for x in (cal.get("weekly_blocks") or []) if isinstance(x, dict)]
    day_plan = _day_entry(days, day_n) if day_n <= 14 else None
    week_plan = _week_block_for_day(weeks, day_for_blocks)
    return {
        "active": True,
        "calendar_day_since_live_start": day_n,
        "post_first_60_window": post_window,
        "calendar_day_for_plan_lookup": day_for_blocks,
        "phase_label": (day_plan or week_plan or {}).get("phase_label") or cal.get("default_phase_label"),
        "objective_today": (day_plan or {}).get("objective") or (week_plan or {}).get("operating_priority"),
        "max_open_notional_usd_aggregate": (day_plan or {}).get("max_open_notional_usd_aggregate")
        or (week_plan or {}).get("notional_policy"),
        "max_trades": (day_plan or {}).get("max_trades") or (week_plan or {}).get("trade_count_target_range"),
        "notes_if_post_window": "Use last weekly block as advisory; re-baseline calendar if continuing structured ops."
        if post_window
        else None,
        "success_criteria": (day_plan or {}).get("success") or (week_plan or {}).get("validation_focus"),
        "pause_triggers": (day_plan or {}).get("pause_or_review_triggers"),
        "artifacts_to_write": (day_plan or {}).get("databank_and_reports"),
        "ceo_evening_review_focus": (day_plan or {}).get("ceo_evening_review"),
        "control_refs": {
            "scaling_gates": "data/control/scaling_gates.json",
            "halt_rules": "data/control/halt_rules.json",
            "dashboard_thresholds": "data/control/live_dashboard_thresholds.json",
            "daily_template": "data/control/daily_ceo_review_template.json",
            "weekly_template": "data/control/weekly_ceo_review_template.json",
        },
    }


def write_first_60_day_daily_envelope(
    diagnosis: Optional[Mapping[str, Any]] = None,
    *,
    runtime_root: Optional[Path] = None,
    as_of: Optional[date] = None,
    skip_if_same_day: bool = False,
) -> Dict[str, Any]:
    """Writes ``data/review/first_60_day_daily_envelope.json`` (deterministic, advisory)."""
    ensure_first_60_day_control_artifacts(runtime_root=runtime_root)
    as_of = as_of or _utc_today()
    cdir = control_data_dir(runtime_root)
    stamp_p = cdir / "first_60_daily_envelope_last_date.json"
    if skip_if_same_day:
        prev = _load_json(stamp_p) or {}
        if str(prev.get("as_of_date") or "") == as_of.isoformat():
            return {"skipped": True, "reason": "same_utc_day", "as_of_date": as_of.isoformat()}
    day_n = live_calendar_day_number(runtime_root=runtime_root, as_of=as_of)
    diag = diagnosis or {}
    ctx = attach_first_60_context_for_ceo_review(diag, runtime_root=runtime_root)
    daily_tpl = _load_json(control_data_dir(runtime_root) / "daily_ceo_review_template.json")
    envelope = {
        "truth_version": "first_60_day_daily_envelope_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of_date": as_of.isoformat(),
        "live_ops": ctx,
        "diagnosis_refs": {
            "health": diag.get("health"),
            "date": diag.get("date"),
            "biggest_risk": diag.get("biggest_risk"),
        },
        "questions_from_template": (daily_tpl or {}).get("required_questions", []),
    }
    rdir = review_data_dir() if runtime_root is None else (Path(runtime_root).resolve() / "data" / "review")
    rdir.mkdir(parents=True, exist_ok=True)
    out = rdir / "first_60_day_daily_envelope.json"
    out.write_text(json.dumps(envelope, indent=2, default=str), encoding="utf-8")
    stamp_p.write_text(
        json.dumps({"as_of_date": as_of.isoformat(), "generated_at": envelope["generated_at"]}, indent=2),
        encoding="utf-8",
    )
    return envelope


def write_first_60_day_weekly_envelope_if_due(
    *,
    runtime_root: Optional[Path] = None,
    as_of: Optional[date] = None,
) -> Dict[str, Any]:
    """At most one weekly envelope per ISO week — ``data/review/first_60_day_weekly_envelope.json``."""
    ensure_first_60_day_control_artifacts(runtime_root=runtime_root)
    as_of = as_of or _utc_today()
    y, w, _ = as_of.isocalendar()
    week_id = f"{y}-W{w:02d}"
    cdir = control_data_dir(runtime_root)
    roll = _load_json(cdir / "first_60_weekly_roll_state.json") or {}
    if str(roll.get("last_week_id_written") or "") == week_id:
        return {"written": False, "reason": "already_written", "week_id": week_id}
    weekly_tpl = _load_json(cdir / "weekly_ceo_review_template.json")
    day_n = live_calendar_day_number(runtime_root=runtime_root, as_of=as_of)
    body = {
        "truth_version": "first_60_day_weekly_envelope_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "week_id": week_id,
        "calendar_day_since_live_start": day_n,
        "questions_from_template": (weekly_tpl or {}).get("required_questions", []),
    }
    rdir = review_data_dir() if runtime_root is None else (Path(runtime_root).resolve() / "data" / "review")
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "first_60_day_weekly_envelope.json").write_text(json.dumps(body, indent=2, default=str), encoding="utf-8")
    roll_path = cdir / "first_60_weekly_roll_state.json"
    roll_path.write_text(
        json.dumps({"last_week_id_written": week_id, "updated_at": body["generated_at"]}, indent=2),
        encoding="utf-8",
    )
    return {"written": True, "week_id": week_id, "path": str(rdir / "first_60_day_weekly_envelope.json")}


def run_first_60_live_ops_tick(
    *,
    runtime_root: Optional[Path] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """
    One automation step: seed control files, daily envelope (deduped per UTC day unless ``force``),
    weekly envelope if due, heartbeat.

    Safe at high cadence: daily file is not rewritten every tick unless ``force`` or new day.
    """
    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    os.environ.setdefault("EZRAS_RUNTIME_ROOT", str(root))
    seed = ensure_first_60_day_control_artifacts(runtime_root=root)
    diag: Optional[Dict[str, Any]] = None
    dp = root / "data" / "review" / "daily_diagnosis.json"
    if dp.is_file():
        try:
            raw = json.loads(dp.read_text(encoding="utf-8"))
            diag = raw if isinstance(raw, dict) else None
        except (json.JSONDecodeError, OSError):
            diag = None
    daily = write_first_60_day_daily_envelope(
        diag or {},
        runtime_root=root,
        skip_if_same_day=not bool(force),
    )
    weekly = write_first_60_day_weekly_envelope_if_due(runtime_root=root)
    hb_path = control_data_dir(runtime_root=root) / "first_60_live_ops_heartbeat.json"
    body = {
        "truth_version": "first_60_live_ops_heartbeat_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(root),
        "seed_summary": seed,
        "daily_envelope": {"skipped": daily.get("skipped", False), "as_of_date": daily.get("as_of_date")},
        "weekly_envelope": weekly,
    }
    hb_path.write_text(json.dumps(body, indent=2, default=str), encoding="utf-8")
    return {"ok": True, "heartbeat_path": str(hb_path), "daily": daily, "weekly": weekly, "seed": seed}


def run_first_60_live_ops_daemon_forever(
    *,
    runtime_root: Optional[Path] = None,
    interval_sec: Optional[float] = None,
) -> None:
    """
    Process-level infinite loop (SIGINT/SIGTERM stops). Uses ``EZRAS_FIRST_60_DAEMON_SLEEP_SEC`` or ``interval_sec``.
    """
    from trading_ai.runtime.operating_system import enforce_non_live_env_defaults

    enforce_non_live_env_defaults()
    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)
    raw_iv = (
        interval_sec
        if interval_sec is not None
        else float((os.environ.get("EZRAS_FIRST_60_DAEMON_SLEEP_SEC") or "120").strip() or "120")
    )
    sleep_sec = max(5.0, float(raw_iv))
    stop = False

    def _sig(*_a: Any) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)
    while not stop:
        try:
            run_first_60_live_ops_tick(runtime_root=root, force=False)
        except Exception as exc:
            err_p = control_data_dir(runtime_root=root) / "first_60_live_ops_last_error.json"
            err_p.write_text(
                json.dumps(
                    {"ts": datetime.now(timezone.utc).isoformat(), "error": type(exc).__name__, "detail": str(exc)},
                    indent=2,
                ),
                encoding="utf-8",
            )
        time.sleep(sleep_sec)
