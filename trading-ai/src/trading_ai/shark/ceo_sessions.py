"""Claude CEO autonomous strategy sessions — 4× daily ET briefings, live parameter tweaks, audit trail."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from trading_ai.governance.storage_architecture import shark_state_path
from trading_ai.llm.anthropic_defaults import DEFAULT_ANTHROPIC_MESSAGES_MODEL
from trading_ai.shark.dotenv_load import load_shark_dotenv

load_shark_dotenv()

logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore[misc, assignment]

ET = ZoneInfo("America/New_York") if ZoneInfo else None

CEO_SESSION_TIMES = ["08:00", "12:00", "17:00", "22:00"]

_MAX_SESSION_HISTORY = 500
_MAX_PIPELINE = 200

CEO_EDGE_OVERRIDES: Dict[str, float] = {}

_SESSION_PATH = "ceo_sessions.json"
_OVERRIDES_PATH = "ceo_overrides.json"
_PIPELINE_PATH = "strategy_pipeline.json"
_SCAN_DAY_PATH = "ceo_scan_day.json"
_ANCHOR_PATH = "ceo_runtime_anchor.json"


def _now_et() -> datetime:
    if ET is not None:
        return datetime.now(tz=ET)
    return datetime.utcnow().replace(tzinfo=None)  # type: ignore[return-value]


def ceo_sessions_path() -> Any:
    return shark_state_path(_SESSION_PATH)


def ceo_overrides_path() -> Any:
    return shark_state_path(_OVERRIDES_PATH)


def strategy_pipeline_path() -> Any:
    return shark_state_path(_PIPELINE_PATH)


def ceo_scan_day_path() -> Any:
    return shark_state_path(_SCAN_DAY_PATH)


def ceo_runtime_anchor_path() -> Any:
    return shark_state_path(_ANCHOR_PATH)


def bump_daily_scan_stats(markets: int, execution_attempts: int) -> None:
    """Increment per-day scan counters (called from scan cycles)."""
    if markets <= 0 and execution_attempts <= 0:
        return
    p = ceo_scan_day_path()
    day = _now_et().strftime("%Y-%m-%d")
    data: Dict[str, Any] = {"day": day, "markets_scanned": 0, "execution_attempts": 0}
    if p.is_file():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and raw.get("day") == day:
                data = raw
        except (OSError, json.JSONDecodeError):
            pass
    if data.get("day") != day:
        data = {"day": day, "markets_scanned": 0, "execution_attempts": 0}
    data["markets_scanned"] = int(data.get("markets_scanned", 0)) + int(markets)
    data["execution_attempts"] = int(data.get("execution_attempts", 0)) + int(execution_attempts)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _ensure_runtime_anchor() -> float:
    p = ceo_runtime_anchor_path()
    if p.is_file():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and raw.get("anchor_unix"):
                return float(raw["anchor_unix"])
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    anchor = time.time()
    p.write_text(json.dumps({"anchor_unix": anchor, "created": _now_et().isoformat()}, indent=2), encoding="utf-8")
    return anchor


def load_session_history() -> List[Dict[str, Any]]:
    p = ceo_sessions_path()
    if not p.is_file():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return [x for x in raw if isinstance(x, dict)]
    except (OSError, json.JSONDecodeError):
        pass
    return []


def save_session_history(record: Dict[str, Any]) -> None:
    hist = load_session_history()
    hist.append(record)
    if len(hist) > _MAX_SESSION_HISTORY:
        hist = hist[-_MAX_SESSION_HISTORY :]
    ceo_sessions_path().write_text(json.dumps(hist, indent=2, default=str), encoding="utf-8")


def load_strategy_pipeline() -> List[Dict[str, Any]]:
    p = strategy_pipeline_path()
    if not p.is_file():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return [x for x in raw if isinstance(x, dict)]
    except (OSError, json.JSONDecodeError):
        pass
    return []


def save_strategy_pipeline(items: List[Dict[str, Any]]) -> None:
    if len(items) > _MAX_PIPELINE:
        items = items[-_MAX_PIPELINE :]
    strategy_pipeline_path().write_text(json.dumps(items, indent=2, default=str), encoding="utf-8")


def append_strategies_to_pipeline(strategies: List[Dict[str, Any]], *, proposed_by: str) -> None:
    if not strategies:
        return
    pipe = load_strategy_pipeline()
    ts = _now_et().isoformat()
    for s in strategies:
        if not isinstance(s, dict):
            continue
        pipe.append(
            {
                "proposed_at": ts,
                "proposed_by": proposed_by,
                "name": str(s.get("name", "unnamed")),
                "description": str(s.get("description", "")),
                "implementation": str(s.get("implementation", "")),
                "expected_edge": float(s.get("expected_edge", 0) or 0),
                "priority": str(s.get("priority", "medium")),
                "status": "proposed",
                "test_results": {},
                "implemented_at": None,
            }
        )
    save_strategy_pipeline(pipe)


def load_ceo_overrides_into_memory() -> None:
    global CEO_EDGE_OVERRIDES
    p = ceo_overrides_path()
    if not p.is_file():
        CEO_EDGE_OVERRIDES = {}
        return
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("min_edge_changes"), dict):
            CEO_EDGE_OVERRIDES = {str(k): float(v) for k, v in raw["min_edge_changes"].items()}
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        CEO_EDGE_OVERRIDES = {}


def save_ceo_overrides() -> None:
    payload = {"min_edge_changes": dict(CEO_EDGE_OVERRIDES), "updated_at": _now_et().isoformat()}
    ceo_overrides_path().write_text(json.dumps(payload, indent=2), encoding="utf-8")


def get_all_trades() -> List[Dict[str, Any]]:
    from trading_ai.shark.state_store import load_positions

    data = load_positions()
    hist = data.get("history") or []
    if isinstance(hist, list):
        return [x for x in hist if isinstance(x, dict)]
    return []


def _closed_at_unix(entry: Dict[str, Any]) -> float:
    v = entry.get("closed_at")
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _day_bounds_et(day_str: str) -> tuple[float, float]:
    """Return UTC unix range for America/New_York calendar day ``day_str``."""
    if ET is None:
        d0 = datetime.strptime(day_str, "%Y-%m-%d")
        start = d0.replace(tzinfo=None)
        end = start + timedelta(days=1)
        return start.timestamp(), end.timestamp()
    d0 = datetime.strptime(day_str, "%Y-%m-%d").replace(tzinfo=ET)
    start = d0
    end = d0 + timedelta(days=1)
    return start.timestamp(), end.timestamp()


def get_summary_stats(day_str: str) -> Dict[str, Any]:
    from trading_ai.shark.state_store import load_capital

    rec = load_capital()
    anchor = _ensure_runtime_anchor()
    days_running = max(1, int((time.time() - anchor) / 86400.0))

    t0, t1 = _day_bounds_et(day_str)
    hist = get_all_trades()
    today_trades = [h for h in hist if t0 <= _closed_at_unix(h) < t1]
    wins = sum(1 for h in today_trades if float(h.get("pnl", 0) or 0) > 0)
    losses = sum(1 for h in today_trades if float(h.get("pnl", 0) or 0) < 0)
    pnl_today = sum(float(h.get("pnl", 0) or 0) for h in today_trades)
    n_t = len(today_trades)
    win_rate = (wins / n_t) if n_t else 0.0

    hunt_wins: Dict[str, int] = {}
    hunt_tot: Dict[str, int] = {}
    cat_wins: Dict[str, int] = {}
    cat_tot: Dict[str, int] = {}
    for h in today_trades:
        cats = str(h.get("market_category") or "unknown")
        cat_tot[cats] = cat_tot.get(cats, 0) + 1
        if float(h.get("pnl", 0) or 0) > 0:
            cat_wins[cats] = cat_wins.get(cats, 0) + 1
        for ht in h.get("hunt_types") or []:
            k = str(ht)
            hunt_tot[k] = hunt_tot.get(k, 0) + 1
            if float(h.get("pnl", 0) or 0) > 0:
                hunt_wins[k] = hunt_wins.get(k, 0) + 1

    def _rates(w: Dict[str, int], t: Dict[str, int]) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for k, tot in t.items():
            if tot:
                out[k] = w.get(k, 0) / tot
        return out

    hunt_rates = _rates(hunt_wins, hunt_tot)
    best_hunt = dict(sorted(hunt_rates.items(), key=lambda kv: kv[1], reverse=True)[:5])
    worst_hunt = dict(sorted(hunt_rates.items(), key=lambda kv: kv[1])[:5])
    cat_rates = _rates(cat_wins, cat_tot)

    scan = {"markets_scanned": 0, "execution_attempts": 0, "day": day_str}
    sp = ceo_scan_day_path()
    if sp.is_file():
        try:
            raw = json.loads(sp.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and raw.get("day") == day_str:
                scan["markets_scanned"] = int(raw.get("markets_scanned", 0))
                scan["execution_attempts"] = int(raw.get("execution_attempts", 0))
        except (OSError, json.JSONDecodeError):
            pass
    msc = max(1, scan["markets_scanned"])
    exec_rate = float(scan["execution_attempts"]) / float(msc)

    return {
        "net_worth": float(rec.current_capital),
        "starting": float(rec.starting_capital),
        "days_running": days_running,
        "trades_today": n_t,
        "wins_today": wins,
        "losses_today": losses,
        "win_rate_today": win_rate,
        "pnl_today": pnl_today,
        "best_hunt_types": best_hunt or {},
        "worst_hunt_types": worst_hunt or {},
        "category_win_rates": cat_rates,
        "markets_scanned": scan["markets_scanned"],
        "execution_rate": exec_rate,
        "execution_attempts": scan["execution_attempts"],
    }


def get_weekly_trade_summary() -> Dict[str, Any]:
    """Last 7 days (ET) closed trades — used on Sunday CEO sessions."""
    if ET is None:
        end = datetime.utcnow()
        start = end - timedelta(days=7)
        t_end = end.timestamp()
        t_start = start.timestamp()
    else:
        end = datetime.now(tz=ET)
        start = end - timedelta(days=7)
        t_end = end.timestamp()
        t_start = start.timestamp()
    hist = get_all_trades()
    week = [h for h in hist if t_start <= _closed_at_unix(h) <= t_end]
    pnl = sum(float(h.get("pnl", 0) or 0) for h in week)
    wins = sum(1 for h in week if float(h.get("pnl", 0) or 0) > 0)
    return {"trades": len(week), "wins": wins, "pnl_week": pnl}


def _pipeline_summary_text() -> str:
    pipe = load_strategy_pipeline()
    if not pipe:
        return "(empty)"
    lines: List[str] = []
    for row in pipe[-12:]:
        lines.append(
            f"- {row.get('name')}: status={row.get('status')} edge~{row.get('expected_edge')} | {str(row.get('description', ''))[:80]}"
        )
    return "\n".join(lines)


def build_ceo_session_prompt(
    session_type: str,
    stats: dict,
    recent_trades: list,
    journal_summary: dict,
    session_history: list,
) -> str:
    # Minimal context - only essential A-Z data points
    week_stats = journal_summary.get("weekly_stats") or {}
    
    # Only last 3 trades to save tokens
    recent = recent_trades[-3:] if recent_trades else []
    
    # Only top 2 best/worst hunt types
    best = dict(list((stats.get("best_hunt_types") or {}).items())[:2])
    worst = dict(list((stats.get("worst_hunt_types") or {}).items())[:2])
    
    # Only active strategy names, not full details
    from trading_ai.shark.avenues import load_avenues
    from trading_ai.shark.master_strategies import get_active_strategies
    active_avenues = [k for k, a in load_avenues().items() if (a.status or "").lower() == "active"]
    active_strategies = get_active_strategies(float(stats["net_worth"]), active_avenues)
    active_names = [s.name for s in active_strategies][:5]  # Max 5 strategies

    return f"""CEO BRIEF - {session_type}

CAPITAL: ${stats["net_worth"]:.2f} | P&L TODAY: ${stats["pnl_today"]:+.2f} | WIN RATE: {stats["win_rate_today"]:.1%}
TRADES: {stats["trades_today"]} | SCANNED: {stats["markets_scanned"]}

WEEK: {week_stats.get('trades', 0)} trades, {week_stats.get('wins', 0)} wins, ${float(week_stats.get('pnl_week', 0)):+.2f} P&L

BEST HUNTS: {json.dumps(best, separators=(',', ':'))}
WORST HUNTS: {json.dumps(worst, separators=(',', ':'))}

RECENT TRADES: {json.dumps(recent, separators=(',', ':'), default=str)}

ACTIVE STRATEGIES: {json.dumps(active_names, separators=(',', ':'))}

RESPOND IN JSON:
{{
  "assessment": "1 sentence",
  "working": ["item"],
  "failing": ["item"],
  "new_strategies": [
    {{"name": "name", "description": "desc", "implementation": "code", "expected_edge": 0.0, "priority": "high"}}
  ],
  "parameter_changes": {{
    "hunt_type_adjustments": {{}},
    "min_edge_changes": {{}},
    "sizing_changes": {{}},
    "focus_markets": [],
    "strategy_enabled": {{}}
  }},
  "next_session_target": "1 sentence",
  "confidence": 0.5
}}"""


def _get_anthropic_client() -> Any:
    import anthropic

    api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY unset")
    return anthropic.Anthropic(api_key=api_key)


def _parse_ceo_json_response(text: str) -> Dict[str, Any]:
    try:
        raw = json.loads(text)
        if isinstance(raw, dict):
            return raw
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError("CEO response missing JSON object")
    raw = json.loads(m.group())
    if not isinstance(raw, dict):
        raise ValueError("CEO JSON not an object")
    return raw


def _normalize_ceo_result(raw: Dict[str, Any]) -> Dict[str, Any]:
    pc = raw.get("parameter_changes")
    if not isinstance(pc, dict):
        pc = {}
    return {
        "assessment": str(raw.get("assessment", ""))[:800],
        "working": list(raw.get("working") or []) if isinstance(raw.get("working"), list) else [],
        "failing": list(raw.get("failing") or []) if isinstance(raw.get("failing"), list) else [],
        "new_strategies": list(raw.get("new_strategies") or [])
        if isinstance(raw.get("new_strategies"), list)
        else [],
        "parameter_changes": {
            "hunt_type_adjustments": dict(pc.get("hunt_type_adjustments") or {})
            if isinstance(pc.get("hunt_type_adjustments"), dict)
            else {},
            "min_edge_changes": dict(pc.get("min_edge_changes") or {}) if isinstance(pc.get("min_edge_changes"), dict) else {},
            "sizing_changes": dict(pc.get("sizing_changes") or {}) if isinstance(pc.get("sizing_changes"), dict) else {},
            "focus_markets": list(pc.get("focus_markets") or []) if isinstance(pc.get("focus_markets"), list) else [],
            "strategy_enabled": dict(pc.get("strategy_enabled") or {})
            if isinstance(pc.get("strategy_enabled"), dict)
            else {},
        },
        "next_session_target": str(raw.get("next_session_target", ""))[:400],
        "confidence": float(raw.get("confidence", 0.5) or 0.5),
    }


def apply_ceo_parameter_changes(changes: dict) -> None:
    from trading_ai.shark.state import BAYES
    from trading_ai.shark.state_store import save_bayesian_snapshot

    global CEO_EDGE_OVERRIDES

    for hunt, mult in (changes.get("hunt_type_adjustments") or {}).items():
        try:
            m = float(mult)
        except (TypeError, ValueError):
            continue
        hk = str(hunt)
        current = float(BAYES.hunt_weights.get(hk, 0.5))
        new_weight = max(0.05, min(2.0, current * m))
        BAYES.hunt_weights[hk] = new_weight
        logger.info("CEO adjusted hunt weight %s: %.3f → %.3f", hk, current, new_weight)

    for hunt, new_edge in (changes.get("min_edge_changes") or {}).items():
        try:
            CEO_EDGE_OVERRIDES[str(hunt)] = float(new_edge)
        except (TypeError, ValueError):
            continue
        logger.info("CEO min_edge %s → %s", hunt, CEO_EDGE_OVERRIDES[str(hunt)])

    se = changes.get("strategy_enabled") if isinstance(changes, dict) else None
    if isinstance(se, dict) and se:
        from trading_ai.shark.master_strategies import apply_strategy_enabled_changes

        apply_strategy_enabled_changes(se)

    save_bayesian_snapshot()
    save_ceo_overrides()


def get_ceo_min_edge_floor_for_hunts(hunt_values: List[str]) -> Optional[float]:
    """Return highest applicable CEO min-edge floor for the given hunt type strings, if any."""
    floors: List[float] = []
    for hv in hunt_values:
        k = str(hv)
        if k in CEO_EDGE_OVERRIDES:
            floors.append(float(CEO_EDGE_OVERRIDES[k]))
    if not floors:
        return None
    return max(floors)


def send_ceo_session_telegram(session_type: str, result: Dict[str, Any], stats: Dict[str, Any]) -> None:
    from trading_ai.shark.reporting import send_telegram

    msg = (
        f"🏦 CEO BRIEFING — {session_type}\n"
        f"{'─' * 30}\n\n"
        f"📊 {result.get('assessment', '')}\n\n"
        f"✅ WORKING:\n"
    )
    for w in result.get("working") or []:
        msg += f"  • {w}\n"
    msg += "\n❌ FAILING:\n"
    for f in result.get("failing") or []:
        msg += f"  • {f}\n"
    ns = result.get("new_strategies") or []
    if ns:
        msg += "\n💡 NEW STRATEGIES:\n"
        for s in ns:
            if not isinstance(s, dict):
                continue
            pr = str(s.get("priority", "medium")).upper()
            msg += f"  [{pr}] {s.get('name')}\n  → {s.get('description')}\n"
    msg += (
        f"\n🎯 NEXT TARGET:\n{result.get('next_session_target', '')}\n\n"
        f"Capital: ${float(stats.get('net_worth', 0)):.2f} | P&L today: "
        f"${float(stats.get('pnl_today', 0)):+.2f}"
    )
    try:
        send_telegram(msg)
    except Exception as exc:
        logger.warning("CEO Telegram failed: %s", exc)


def run_ceo_session(session_type: str) -> Dict[str, Any]:
    day = _now_et().strftime("%Y-%m-%d")
    stats = get_summary_stats(day)
    recent_trades = get_all_trades()[-20:]
    history = load_session_history()

    now_et = _now_et()
    journal: Dict[str, Any] = {"week_in_review": False}
    if hasattr(now_et, "weekday") and now_et.weekday() == 6:
        journal["week_in_review"] = True
        journal["weekly_stats"] = get_weekly_trade_summary()

    prompt = build_ceo_session_prompt(
        session_type,
        stats,
        recent_trades,
        journal,
        history,
    )

    model = (os.environ.get("ANTHROPIC_MODEL") or DEFAULT_ANTHROPIC_MESSAGES_MODEL).strip()
    client = _get_anthropic_client()
    response = client.messages.create(
        model=model,
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text
    raw = _parse_ceo_json_response(text)
    result = _normalize_ceo_result(raw)

    session_record = {
        "timestamp": _now_et().isoformat(),
        "session_type": session_type,
        "assessment": result["assessment"],
        "working": result["working"],
        "failing": result["failing"],
        "new_strategies": result["new_strategies"],
        "parameter_changes": result["parameter_changes"],
        "stats_snapshot": stats,
        "confidence": result["confidence"],
    }
    save_session_history(session_record)

    apply_ceo_parameter_changes(result["parameter_changes"])
    append_strategies_to_pipeline(result["new_strategies"], proposed_by=f"CEO session {session_type}")

    send_ceo_session_telegram(session_type, result, stats)
    return result


def run_ceo_session_safe(session_type: str) -> Dict[str, Any]:
    """Run CEO session; on failure return a stub dict (scheduler-friendly)."""
    try:
        return run_ceo_session(session_type)
    except Exception as exc:
        logger.exception("CEO session %s failed: %s", session_type, exc)
        return {
            "assessment": f"Session failed: {exc}",
            "working": [],
            "failing": [],
            "new_strategies": [],
            "parameter_changes": {},
            "next_session_target": "Fix errors and rerun",
            "confidence": 0.0,
            "error": str(exc),
        }
