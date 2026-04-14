"""Universal trade journal (JSON) — all avenues; stats + loss post-mortem source."""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from trading_ai.governance.storage_architecture import shark_state_path
from trading_ai.shark.dotenv_load import load_shark_dotenv

if TYPE_CHECKING:
    from trading_ai.shark.models import ExecutionIntent, OrderResult, ScoredOpportunity
    from trading_ai.shark.models import ConfirmationResult

load_shark_dotenv()

logger = logging.getLogger(__name__)

_JOURNAL_FILE = "trade_journal.json"
_lock = threading.Lock()


def _path() -> Any:
    return shark_state_path(_JOURNAL_FILE)


def _default_root() -> Dict[str, Any]:
    return {
        "trades": [],
        "meta": {"last_journal_loss_analysis_max_resolved_at": 0.0},
    }


def _load_root() -> Dict[str, Any]:
    p = _path()
    if not p.is_file():
        return dict(_default_root())
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return dict(_default_root())
        base = _default_root()
        base["trades"] = raw["trades"] if isinstance(raw.get("trades"), list) else []
        meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
        base["meta"].update(meta)
        return base
    except (OSError, json.JSONDecodeError):
        return dict(_default_root())


def _save_root(data: Dict[str, Any]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp.replace(p)


def _normalize_avenue(outlet: str) -> str:
    o = (outlet or "").strip().lower()
    if o in ("kalshi", "polymarket", "manifold", "fanduel", "draftkings", "tastytrade", "webull"):
        return o
    if o in ("fan duel", "fd"):
        return "fanduel"
    if o in ("dk",):
        return "draftkings"
    return o or "other"


def _normalize_category(cat: Optional[str]) -> str:
    c = (cat or "other").strip().lower()
    allowed = ("crypto", "sports", "politics", "economics", "weather", "other")
    return c if c in allowed else "other"


def _iso_from_unix(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _american_implied_probability(american: float) -> float:
    o = float(american)
    if o >= 100:
        return 100.0 / (o + 100.0)
    if o <= -100:
        return abs(o) / (abs(o) + 100.0)
    return 0.5


def log_trade_opened(
    intent: "ExecutionIntent",
    order_result: Optional["OrderResult"] = None,
    *,
    conf: Optional["ConfirmationResult"] = None,
    scored: Optional["ScoredOpportunity"] = None,
    execution_time_ms: int = 0,
    dry_run: bool = False,
) -> str:
    """Append a pending trade row; returns ``trade_id`` (UUID)."""
    from trading_ai.shark.capital_phase import detect_phase
    from trading_ai.shark.models import ExecutionIntent

    if not isinstance(intent, ExecutionIntent):
        return ""
    tid = str(uuid.uuid4())
    now = time.time()
    dt = datetime.fromtimestamp(now, tz=timezone.utc)
    date_str = dt.strftime("%Y-%m-%d")
    m = scored.market if scored is not None else None
    question = ""
    category = "other"
    if m is not None:
        question = str(
            getattr(m, "question_text", None) or m.resolution_criteria or intent.market_id
        )[:2000]
        category = _normalize_category(getattr(m, "market_category", None))
    else:
        question = str(intent.market_id)[:2000]
    hunt = intent.hunt_types[0].value if intent.hunt_types else "unknown"
    entry = float(conf.actual_fill_price) if conf is not None else float(intent.expected_price or 0.5)
    oid = order_result.order_id if order_result is not None else ""
    notes_parts: List[str] = []
    if dry_run:
        notes_parts.append("dry_run")
    if getattr(intent, "is_mana", False):
        notes_parts.append("notional_mana_not_usd")
    try:
        from trading_ai.shark.state_store import load_capital

        rec = load_capital()
        phase_val = detect_phase(float(rec.current_capital)).value
    except Exception:
        phase_val = detect_phase(float(intent.notional_usd * 10 or 100)).value
    row: Dict[str, Any] = {
        "trade_id": tid,
        "timestamp": _iso_from_unix(now),
        "date": date_str,
        "avenue": _normalize_avenue(intent.outlet),
        "market_id": str(intent.market_id),
        "question": question,
        "category": category,
        "hunt_type": hunt,
        "side": str(intent.side).upper(),
        "edge_detected": round(float(intent.edge_after_fees), 6),
        "position_size_usd": round(float(intent.notional_usd), 4),
        "entry_price": round(entry, 6),
        "exit_price": 0.0,
        "pnl_usd": 0.0,
        "pnl_pct": 0.0,
        "outcome": "pending",
        "claude_decision": str(intent.meta.get("claude_decision") or "")[:32] or None,
        "claude_reasoning": str(intent.meta.get("claude_reasoning") or "")[:2000] or None,
        "claude_confidence": float(intent.meta.get("claude_confidence"))
        if intent.meta.get("claude_confidence") is not None
        else None,
        "execution_time_ms": int(execution_time_ms),
        "resolved_at": None,
        "phase": phase_val,
        "notes": "|".join(notes_parts),
        "order_id": oid,
    }

    with _lock:
        root = _load_root()
        trades: List[Dict[str, Any]] = list(root.get("trades") or [])
        trades.append(row)
        if len(trades) > 20000:
            trades = trades[-20000:]
        root["trades"] = trades
        _save_root(root)
    return tid


def log_trade_resolved(
    trade_id: str,
    exit_price: float,
    pnl_usd: float,
    outcome: str,
    *,
    resolved_at_iso: Optional[str] = None,
) -> None:
    """Update journal row by ``trade_id``."""
    if not trade_id:
        return
    out = str(outcome).strip().lower()
    if out not in ("win", "loss", "pending"):
        out = "loss" if out in ("lose", "l") else "win" if out in ("w", "won") else "pending"
    ra = resolved_at_iso or _iso_from_unix(time.time())
    with _lock:
        root = _load_root()
        trades: List[Dict[str, Any]] = list(root.get("trades") or [])
        for row in trades:
            if str(row.get("trade_id")) != str(trade_id):
                continue
            entry = float(row.get("entry_price") or 0.5)
            size = float(row.get("position_size_usd") or 0.0)
            pct = (pnl_usd / size) if size > 1e-9 else 0.0
            row["exit_price"] = round(float(exit_price), 6)
            row["pnl_usd"] = round(float(pnl_usd), 4)
            row["pnl_pct"] = round(float(pct), 6)
            row["outcome"] = out
            row["resolved_at"] = ra
            break
        root["trades"] = trades
        _save_root(root)


def log_sports_trade(
    platform: str,
    pick: str,
    odds: float,
    stake: float,
    outcome: str,
    pnl: float,
    *,
    category: str = "sports",
) -> str:
    """Log a manual sports result (FanDuel / DraftKings). ``odds`` = American (e.g. -110, +150)."""
    tid = str(uuid.uuid4())
    now = time.time()
    dt = datetime.fromtimestamp(now, tz=timezone.utc)
    date_str = dt.strftime("%Y-%m-%d")
    av = _normalize_avenue(platform)
    out = "win" if str(outcome).lower() == "win" else "loss"
    entry = _american_implied_probability(odds)
    row: Dict[str, Any] = {
        "trade_id": tid,
        "timestamp": _iso_from_unix(now),
        "date": date_str,
        "avenue": av,
        "market_id": str(pick)[:500],
        "question": str(pick)[:2000],
        "category": _normalize_category(category),
        "hunt_type": "sports_manual",
        "side": "OVER",
        "edge_detected": 0.0,
        "position_size_usd": round(float(stake), 4),
        "entry_price": round(entry, 6),
        "exit_price": 1.0 if out == "win" else 0.0,
        "pnl_usd": round(float(pnl), 4),
        "pnl_pct": round(float(pnl) / max(stake, 1e-9), 6),
        "outcome": out,
        "claude_decision": None,
        "claude_reasoning": None,
        "claude_confidence": None,
        "execution_time_ms": 0,
        "resolved_at": _iso_from_unix(now),
        "phase": "sports_manual",
        "notes": f"american_odds={odds}",
    }
    with _lock:
        root = _load_root()
        trades = list(root.get("trades") or [])
        trades.append(row)
        if len(trades) > 20000:
            trades = trades[-20000:]
        root["trades"] = trades
        _save_root(root)
    return tid


def get_trades_for_date(date_str: str) -> List[Dict[str, Any]]:
    root = _load_root()
    return [t for t in root.get("trades") or [] if isinstance(t, dict) and str(t.get("date")) == date_str]


def get_all_trades() -> List[Dict[str, Any]]:
    root = _load_root()
    return [t for t in root.get("trades") or [] if isinstance(t, dict)]


def get_summary_stats(date_str: Optional[str] = None) -> Dict[str, Any]:
    trades = get_trades_for_date(date_str) if date_str else get_all_trades()
    closed = [t for t in trades if str(t.get("outcome", "pending")).lower() != "pending"]
    wins = [t for t in closed if str(t.get("outcome")).lower() == "win"]
    losses = [t for t in closed if str(t.get("outcome")).lower() == "loss"]
    total_pnl = sum(float(t.get("pnl_usd", 0) or 0) for t in closed)
    best = max(closed, key=lambda t: float(t.get("pnl_usd", 0) or 0), default=None)
    worst = min(closed, key=lambda t: float(t.get("pnl_usd", 0) or 0), default=None)
    by_avenue: Dict[str, Dict[str, Any]] = {}
    by_hunt: Dict[str, Dict[str, Any]] = {}
    by_cat: Dict[str, Dict[str, Any]] = {}

    def _bump(bucket: Dict[str, Dict[str, Any]], key: str, t: Dict[str, Any]) -> None:
        b = bucket.setdefault(key, {"n": 0, "wins": 0, "losses": 0, "pnl": 0.0})
        b["n"] += 1
        o = str(t.get("outcome", "")).lower()
        if o == "win":
            b["wins"] += 1
        elif o == "loss":
            b["losses"] += 1
        b["pnl"] += float(t.get("pnl_usd", 0) or 0)

    for t in closed:
        _bump(by_avenue, str(t.get("avenue", "unknown")), t)
        _bump(by_hunt, str(t.get("hunt_type", "unknown")), t)
        _bump(by_cat, str(t.get("category", "other")), t)

    n = len(closed)
    wr = (len(wins) / n) if n else 0.0
    return {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(wr, 6),
        "total_pnl": round(total_pnl, 4),
        "best_trade": dict(best) if best else {},
        "worst_trade": dict(worst) if worst else {},
        "by_avenue": by_avenue,
        "by_hunt_type": by_hunt,
        "by_category": by_cat,
    }


def get_full_loss_postmortem(days: int = 7) -> Dict[str, Any]:
    """All losing closed trades in the journal for the last ``days`` (all avenues)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
    losses: List[Dict[str, Any]] = []
    for t in get_all_trades():
        if str(t.get("outcome", "")).lower() != "loss":
            continue
        ts = str(t.get("resolved_at") or t.get("timestamp") or "")
        try:
            tdt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if tdt.tzinfo is None:
                tdt = tdt.replace(tzinfo=timezone.utc)
            if tdt < cutoff:
                continue
        except (TypeError, ValueError):
            continue
        losses.append(dict(t))
    losing_hunt_types: Dict[str, int] = {}
    losing_sides: Dict[str, int] = {}
    edges: List[float] = []
    for row in losses:
        ht = str(row.get("hunt_type") or "unknown")
        losing_hunt_types[ht] = losing_hunt_types.get(ht, 0) + 1
        sd = str(row.get("side") or "UNKNOWN").upper()
        losing_sides[sd] = losing_sides.get(sd, 0) + 1
        try:
            edges.append(float(row.get("edge_detected", 0) or 0))
        except (TypeError, ValueError):
            edges.append(0.0)
    pnl_sum = sum(float(r.get("pnl_usd", 0) or 0) for r in losses)
    total_abs_pnl = round(sum(abs(float(r.get("pnl_usd", 0) or 0)) for r in losses), 2)
    avg_edge = round(sum(edges) / len(edges), 6) if edges else 0.0
    return {
        "total_losses": len(losses),
        "total_mana_lost": round(abs(pnl_sum), 2) if pnl_sum < 0 else total_abs_pnl,
        "losing_hunt_types": losing_hunt_types,
        "losing_sides": losing_sides,
        "avg_edge_on_losses": avg_edge,
        "lessons": [],
        "losses": losses,
        "source": "trade_journal_all_venues",
        "days": days,
    }


def maybe_run_journal_loss_learning_on_startup() -> Dict[str, Any]:
    """Claude analysis for journal losses (non-mana paths); idempotent per resolved batch."""
    post = get_full_loss_postmortem(days=7)
    losses = post.get("losses") or []
    if not losses:
        return {"ran": False, "reason": "no_journal_losses"}
    max_rt = 0.0
    for x in losses:
        ra = str(x.get("resolved_at") or x.get("timestamp") or "")
        try:
            max_rt = max(max_rt, datetime.fromisoformat(ra.replace("Z", "+00:00")).timestamp())
        except (TypeError, ValueError):
            continue
    with _lock:
        root = _load_root()
        meta = dict(root.get("meta") or {})
        last = float(meta.get("last_journal_loss_analysis_max_resolved_at", 0) or 0)
    if max_rt <= 0:
        return {"ran": False, "reason": "no_resolved_timestamps"}
    if max_rt <= last:
        return {"ran": False, "reason": "no_new_journal_losses"}
    from trading_ai.shark.claude_eval import claude_analyze_journal_losses
    from trading_ai.shark.mana_sandbox import apply_claude_learnings

    analysis = claude_analyze_journal_losses(post)
    apply_claude_learnings(analysis)
    try:
        from trading_ai.shark.reporting import send_loss_postmortem_alert

        send_loss_postmortem_alert(post, analysis)
    except Exception as exc:
        logger.warning("journal loss telegram failed: %s", exc)
    with _lock:
        root = _load_root()
        meta = dict(root.get("meta") or {})
        meta["last_journal_loss_analysis_max_resolved_at"] = max_rt
        root["meta"] = meta
        _save_root(root)
    return {"ran": True, "losses_analyzed": len(losses), "max_resolved_at": max_rt}


def exit_price_for_binary_side(side: str, resolution: str) -> float:
    """Settlement mark for the traded side (YES/NO) given market resolution string."""
    r = str(resolution or "").strip().upper()
    yes_win = r in ("YES", "Y", "TRUE", "1", "WIN")
    if str(side).lower() == "yes":
        return 1.0 if yes_win else 0.0
    return 0.0 if yes_win else 1.0
