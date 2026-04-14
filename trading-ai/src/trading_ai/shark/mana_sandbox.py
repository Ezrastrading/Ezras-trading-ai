"""Manifold mana — silent learning sandbox (no Telegram, no capital.json)."""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from trading_ai.governance.storage_architecture import shark_state_path
from trading_ai.shark.dotenv_load import load_shark_dotenv
from trading_ai.shark.execution_live import ezras_dry_run_from_env

load_shark_dotenv()

logger = logging.getLogger(__name__)

STATE_FILE = "mana_sandbox.json"
GROWTH_MULTIPLIER = 7.0
MANA_RECOVERY_BALANCE_FRACTION = 0.80
MANA_RECOVERY_MIN_EDGE = 0.05
MANA_RECOVERY_MAX_STAKE_FRACTION = 0.05
MANA_RECOVERY_MIN_CERTAINTY = 0.93
_HISTORY_CAP = 500


def _path() -> Any:
    return shark_state_path(STATE_FILE)


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_state() -> Dict[str, Any]:
    start = 967.56
    mt = round(start * GROWTH_MULTIPLIER, 2)
    return {
        "mana_balance": start,
        "mana_starting": start,
        "mana_peak": start,
        "total_mana_trades": 0,
        "winning_mana_trades": 0,
        "mana_win_rate": None,
        "strategy_performance": {},
        "last_updated": _iso(),
        "monthly_target_mana": mt,
        "growth_multiplier": GROWTH_MULTIPLIER,
        "open_mana_positions": [],
        "mana_resolution_history": [],
        "last_claude_loss_analysis_max_resolved_at": 0.0,
        "mana_claude_min_edge_overrides": {},
    }


def load_mana_state() -> Dict[str, Any]:
    p = _path()
    if not p.is_file():
        return dict(_default_state())
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return dict(_default_state())
        base = _default_state()
        for k, v in base.items():
            raw.setdefault(k, v)
        raw.setdefault("open_mana_positions", [])
        raw.setdefault("mana_resolution_history", [])
        raw.setdefault("last_claude_loss_analysis_max_resolved_at", 0.0)
        raw.setdefault("mana_claude_min_edge_overrides", {})
        return raw
    except (OSError, json.JSONDecodeError):
        return dict(_default_state())


def save_mana_state(data: Dict[str, Any]) -> None:
    data["last_updated"] = _iso()
    p = _path()
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_mana_summary() -> Dict[str, Any]:
    """Full mana performance snapshot (weekly / CLI)."""
    return dict(load_mana_state())


def top_mana_strategy(perf: Dict[str, Any]) -> str:
    if not perf:
        return "n/a"
    best = None
    best_w = -1.0
    for name, row in perf.items():
        if not isinstance(row, dict):
            continue
        w = float(row.get("wins", 0) or 0)
        if w > best_w:
            best_w = w
            best = name
    return str(best) if best else "n/a"


def execute_mana_trade(intent: Any, *, scored: Any = None) -> bool:
    """
    Submit real Manifold bet (mana). Silent — no Telegram, only mana_sandbox.json.
    """
    from trading_ai.shark.execution_live import confirm_execution
    from trading_ai.shark.manifold_live import submit_manifold_bet
    from trading_ai.shark.models import ExecutionIntent

    if not isinstance(intent, ExecutionIntent):
        return False

    st = load_mana_state()
    mana_cap = float(st.get("mana_balance", 0) or 0)
    if intent.notional_usd > mana_cap + 1e-6:
        logger.debug("mana sandbox: insufficient mana (need %.2f have %.2f)", intent.notional_usd, mana_cap)
        return False

    if ezras_dry_run_from_env():
        logger.info("mana sandbox: dry run — no Manifold submit")
        return False

    try:
        order = submit_manifold_bet(intent)
    except Exception as exc:
        logger.warning("mana sandbox submit failed: %s", exc)
        return False

    conf = confirm_execution(order, intent)
    if not conf.confirmed:
        return False

    st["mana_balance"] = round(mana_cap - float(intent.notional_usd), 2)
    pos = {
        "position_id": str(uuid.uuid4()),
        "market_id": intent.market_id,
        "order_id": order.order_id,
        "side": intent.side,
        "entry_price": conf.actual_fill_price,
        "notional_mana": float(intent.notional_usd),
        "shares": float(conf.actual_fill_size),
        "hunt_types": [h.value for h in intent.hunt_types],
        "strategy_key": "shark_default",
        "opened_at": time.time(),
        "edge_after_fees": float(intent.edge_after_fees),
    }
    for k in ("claude_reasoning", "claude_confidence", "claude_true_probability", "claude_decision"):
        v = intent.meta.get(k)
        if v is not None:
            pos[k] = v
    openp: List[Dict[str, Any]] = list(st.get("open_mana_positions") or [])
    openp.append(pos)
    st["open_mana_positions"] = openp
    if st["mana_balance"] > float(st.get("mana_peak", 0) or 0):
        st["mana_peak"] = st["mana_balance"]
    save_mana_state(st)
    logger.info("mana sandbox: bet placed %s order=%s", intent.market_id, order.order_id)
    return True


def update_mana_outcome(
    market_id: str,
    outcome: str,
    pnl_mana: float,
    *,
    strategy: str = "shark_default",
    hunt_types: Optional[List[Any]] = None,
    win: bool,
    position_side: Optional[str] = None,
    claude_true_probability: Optional[float] = None,
    claude_decision: Optional[str] = None,
) -> None:
    """Record mana outcome, update Bayesian (shared with real trades). No capital.json."""
    from trading_ai.shark.execution import hook_post_trade_resolution
    from trading_ai.shark.models import HuntType

    st = load_mana_state()
    total = int(st.get("total_mana_trades", 0) or 0) + 1
    wins = int(st.get("winning_mana_trades", 0) or 0) + (1 if win else 0)
    st["total_mana_trades"] = total
    st["winning_mana_trades"] = wins
    st["mana_win_rate"] = round(wins / max(total, 1), 4)
    st["mana_balance"] = round(float(st.get("mana_balance", 0) or 0) + float(pnl_mana), 2)
    if st["mana_balance"] > float(st.get("mana_peak", 0) or 0):
        st["mana_peak"] = st["mana_balance"]

    perf = dict(st.get("strategy_performance") or {})
    row = dict(perf.get(strategy) or {"wins": 0, "losses": 0, "pnl_mana": 0.0})
    if win:
        row["wins"] = int(row.get("wins", 0)) + 1
    else:
        row["losses"] = int(row.get("losses", 0)) + 1
    row["pnl_mana"] = round(float(row.get("pnl_mana", 0) or 0) + float(pnl_mana), 2)
    perf[strategy] = row
    st["strategy_performance"] = perf

    save_mana_state(st)

    hts: List[Any] = list(hunt_types or [HuntType.STRUCTURAL_ARBITRAGE])
    hook_post_trade_resolution(
        f"mana-{market_id}",
        win=win,
        strategy=strategy,
        hunt_types=hts,
        outlet="manifold",
        market_id=market_id,
        market_category="mana_sandbox",
        pnl_dollars=float(pnl_mana),
        update_capital=False,
        is_mana=True,
        claude_true_probability=claude_true_probability,
        claude_decision=claude_decision,
        position_side=position_side,
    )


def tick_mana_resolutions() -> int:
    """Poll open mana positions; return count resolved."""
    import json as _json
    import urllib.error
    import urllib.parse
    import urllib.request

    from trading_ai.shark.models import HuntType

    st = load_mana_state()
    openp: List[Dict[str, Any]] = list(st.get("open_mana_positions") or [])
    if not openp:
        return 0

    remaining: List[Dict[str, Any]] = []
    n = 0
    for pos in openp:
        mid = str(pos.get("market_id", ""))
        cid = mid.replace("manifold:", "")
        url = f"https://api.manifold.markets/v0/market/{urllib.parse.quote(cid, safe='')}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "EzrasShark/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                j = _json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            remaining.append(pos)
            continue
        if not j.get("isResolved"):
            remaining.append(pos)
            continue
        res = str(j.get("resolution") or "YES")
        side = str(pos.get("side", "yes"))
        entry = float(pos.get("entry_price", 0.5) or 0.5)
        notional = float(pos.get("notional_mana", 0) or 0)
        from trading_ai.shark.execution_live import calculate_pnl
        from trading_ai.shark.models import OpenPosition

        op = OpenPosition(
            position_id=str(pos.get("position_id", "")),
            outlet="manifold",
            market_id=mid,
            side=side,
            entry_price=entry,
            shares=float(pos.get("shares", 0) or 0),
            notional_usd=notional,
            order_id=str(pos.get("order_id", "")),
            opened_at=float(pos.get("opened_at", 0) or 0),
            hunt_types=list(pos.get("hunt_types") or []),
            expected_edge=0.0,
        )
        pnl = calculate_pnl(op, res)
        win = pnl > 0
        hraw = pos.get("hunt_types") or []
        hts: List[Any] = []
        for x in hraw:
            try:
                hts.append(HuntType(str(x)) if isinstance(x, str) else x)
            except ValueError:
                hts.append(HuntType.STRUCTURAL_ARBITRAGE)
        if not hts:
            hts = [HuntType.STRUCTURAL_ARBITRAGE]
        ctp = pos.get("claude_true_probability")
        ctp_f = float(ctp) if ctp is not None else None
        update_mana_outcome(
            mid,
            res,
            pnl,
            strategy=str(pos.get("strategy_key", "shark_default")),
            hunt_types=hts,
            win=win,
            position_side=side,
            claude_true_probability=ctp_f,
            claude_decision=str(pos.get("claude_decision")) if pos.get("claude_decision") else None,
        )
        hnames = [str(x.value) if hasattr(x, "value") else str(x) for x in hts]
        primary_hunt = hnames[0] if hnames else "unknown"
        rec = {
            "resolved_at": time.time(),
            "market_id": mid,
            "outcome": "loss" if not win else "win",
            "hunt_type_used": primary_hunt,
            "hunt_types": hnames,
            "edge_detected": float(pos.get("edge_after_fees") or 0.0),
            "side_taken": str(side).upper(),
            "mana_staked": notional,
            "mana_lost": float(abs(pnl)) if pnl < 0 else 0.0,
            "mana_pnl": float(pnl),
            "claude_reasoning": pos.get("claude_reasoning"),
            "actual_resolution": res,
            "question_text": (pos.get("question_text") or "")[:400],
        }
        st_hist = load_mana_state()
        hist = list(st_hist.get("mana_resolution_history") or [])
        hist.append(rec)
        if len(hist) > _HISTORY_CAP:
            hist = hist[-_HISTORY_CAP:]
        st_hist["mana_resolution_history"] = hist
        save_mana_state(st_hist)
        n += 1

    st = load_mana_state()
    st["open_mana_positions"] = remaining
    save_mana_state(st)
    return n


def is_mana_recovery_mode() -> bool:
    """True when mana balance is below ``MANA_RECOVERY_BALANCE_FRACTION`` of starting (drawdown guard)."""
    st = load_mana_state()
    start = float(st.get("mana_starting", 0) or 0)
    if start <= 0:
        return False
    bal = float(st.get("mana_balance", 0) or 0)
    return bal < start * MANA_RECOVERY_BALANCE_FRACTION


def is_btc_five_min_market(m: Any) -> bool:
    """Recovery-mode filter: BTC / Bitcoin + ~5 minute window in copy or id."""
    q = (
        str(getattr(m, "question_text", None) or "")
        + " "
        + str(getattr(m, "resolution_criteria", None) or "")
        + " "
        + str(getattr(m, "market_id", "") or "")
    ).lower()
    btc = "btc" in q or "bitcoin" in q
    five = any(
        s in q
        for s in (
            "5 min",
            "5-min",
            "5m ",
            " 5m",
            "five min",
            "5 minute",
            "5-minute",
        )
    )
    return btc and five


def mana_effective_hunt_filter(base: Optional[Any]) -> Optional[Any]:
    """During recovery, only ``NEAR_RESOLUTION`` hunts are considered for mana."""
    from trading_ai.shark.models import HuntType

    if not is_mana_recovery_mode():
        return base
    return {HuntType.NEAR_RESOLUTION}


def mana_effective_min_edge_for_intent(base_edge: float, hunt_labels: List[str]) -> float:
    """Apply Claude min-edge overrides and recovery floor (mana path)."""
    st = load_mana_state()
    ov = dict(st.get("mana_claude_min_edge_overrides") or {})
    extra = 0.0
    for h in hunt_labels:
        v = ov.get(str(h))
        if v is not None:
            try:
                extra = max(extra, float(v))
            except (TypeError, ValueError):
                continue
    if is_mana_recovery_mode():
        return max(float(base_edge), float(MANA_RECOVERY_MIN_EDGE), float(extra))
    return max(float(base_edge), float(extra))


def get_loss_postmortem() -> Dict[str, Any]:
    """Summarize recorded mana resolutions where ``outcome == \"loss\"``."""
    st = load_mana_state()
    hist = [x for x in (st.get("mana_resolution_history") or []) if isinstance(x, dict)]
    losses = [x for x in hist if str(x.get("outcome", "")).lower() == "loss"]
    losing_hunt_types: Dict[str, int] = {}
    losing_sides: Dict[str, int] = {}
    edges: List[float] = []
    lessons: List[str] = []
    for row in losses:
        ht = str(row.get("hunt_type_used") or "unknown")
        losing_hunt_types[ht] = losing_hunt_types.get(ht, 0) + 1
        side = str(row.get("side_taken") or "UNKNOWN").upper()
        losing_sides[side] = losing_sides.get(side, 0) + 1
        try:
            edges.append(float(row.get("edge_detected", 0) or 0))
        except (TypeError, ValueError):
            edges.append(0.0)
    total_mana_lost = round(sum(float(r.get("mana_lost", 0) or 0) for r in losses), 2)
    avg_edge = round(sum(edges) / len(edges), 6) if edges else 0.0
    start = float(st.get("mana_starting", 0) or 0)
    bal = float(st.get("mana_balance", 0) or 0)
    implied = max(0.0, round(start - bal, 2))
    if implied >= 10 and not losses:
        lessons.append(
            "Mana is below starting balance but no per-trade loss history yet; "
            "loss details will populate as Manifold positions resolve."
        )
    return {
        "total_losses": len(losses),
        "total_mana_lost": total_mana_lost if losses else implied,
        "losing_hunt_types": losing_hunt_types,
        "losing_sides": losing_sides,
        "avg_edge_on_losses": avg_edge,
        "lessons": lessons,
        "losses": losses,
    }


def apply_claude_learnings(analysis: Dict[str, Any]) -> None:
    """Penalize hunt weights, persist min-edge overrides, append learnings file."""
    from trading_ai.governance.storage_architecture import shark_state_path
    from trading_ai.shark.state import BAYES
    from trading_ai.shark.state_store import save_bayesian_snapshot

    pc = (analysis or {}).get("parameter_changes") or {}
    for hunt in pc.get("hunt_type_to_disable") or []:
        hk = str(hunt).strip()
        if not hk:
            continue
        logger.warning("Claude disabling hunt (Bayesian weight penalized): %s", hk)
        prev = float(BAYES.hunt_weights.get(hk, 0.5))
        BAYES.hunt_weights[hk] = max(0.05, prev * 0.25)

    st = load_mana_state()
    ov = dict(st.get("mana_claude_min_edge_overrides") or {})
    for hunt, new_edge in (pc.get("min_edge_adjustment") or {}).items():
        hk = str(hunt).strip()
        if not hk:
            continue
        try:
            ne = float(new_edge)
        except (TypeError, ValueError):
            continue
        ov[hk] = ne
        logger.info("Claude adjusting min_edge override: %s → %s", hk, ne)
    st["mana_claude_min_edge_overrides"] = ov
    save_mana_state(st)

    save_bayesian_snapshot()

    learnings_file = shark_state_path("claude_learnings.json")
    learnings: List[Any] = []
    if learnings_file.is_file():
        try:
            raw = json.loads(learnings_file.read_text(encoding="utf-8"))
            learnings = raw if isinstance(raw, list) else []
        except (OSError, json.JSONDecodeError):
            learnings = []
    learnings.append(
        {
            "timestamp": time.time(),
            "trigger": "mana_loss_postmortem",
            "analysis": analysis,
            "applied": True,
        }
    )
    learnings_file.parent.mkdir(parents=True, exist_ok=True)
    learnings_file.write_text(json.dumps(learnings, indent=2), encoding="utf-8")
    logger.info("Claude learnings applied: %s", str((analysis or {}).get("root_cause", ""))[:200])


def maybe_run_mana_loss_learning_on_startup() -> Dict[str, Any]:
    """If new mana losses were recorded since last Claude run, analyze, adapt, alert."""
    post = get_loss_postmortem()
    losses = post.get("losses") or []
    if not losses:
        return {"ran": False, "reason": "no_loss_history"}
    max_rt = max(float(x.get("resolved_at", 0) or 0) for x in losses)
    st = load_mana_state()
    last = float(st.get("last_claude_loss_analysis_max_resolved_at", 0) or 0)
    if max_rt <= last:
        return {"ran": False, "reason": "no_new_losses_since_last_analysis"}

    from trading_ai.shark.claude_eval import claude_analyze_losses

    analysis = claude_analyze_losses(post)
    apply_claude_learnings(analysis)
    try:
        from trading_ai.shark.reporting import send_loss_postmortem_alert

        send_loss_postmortem_alert(post, analysis)
    except Exception as exc:
        logger.warning("loss postmortem telegram failed: %s", exc)

    st = load_mana_state()
    st["last_claude_loss_analysis_max_resolved_at"] = max_rt
    save_mana_state(st)
    return {"ran": True, "losses_analyzed": len(losses), "max_resolved_at": max_rt}
