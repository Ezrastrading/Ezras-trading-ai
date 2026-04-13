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
    }
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
        update_mana_outcome(
            mid,
            res,
            pnl,
            strategy=str(pos.get("strategy_key", "shark_default")),
            hunt_types=hts,
            win=win,
        )
        n += 1

    st = load_mana_state()
    st["open_mana_positions"] = remaining
    save_mana_state(st)
    return n
