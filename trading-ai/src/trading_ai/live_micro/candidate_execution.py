from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def _truthy_env(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def _read_json(p: Path) -> Dict[str, Any]:
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _append_jsonl(p: Path, row: Dict[str, Any]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")


def _load_micro_max_notional(runtime_root: Path) -> float:
    lim = _read_json(runtime_root / "data" / "control" / "live_session_limits.json")
    try:
        return float(lim.get("max_notional_usd") or 0.0)
    except Exception:
        return 0.0


def _pick_candidate_item(cq: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], int]:
    items = list(cq.get("items") or [])
    for i in range(len(items) - 1, -1, -1):
        it = items[i]
        if not isinstance(it, dict):
            continue
        if str(it.get("status") or "new").strip().lower() not in ("new", "queued"):
            continue
        if str(it.get("gate_id") or "").strip().lower() != "gate_b":
            continue
        pid = str(it.get("product_id") or "").strip().upper()
        if not pid:
            continue
        return dict(it), i
    return None, -1


def run_live_micro_candidate_execution_once(*, runtime_root: Path) -> Dict[str, Any]:
    """
    Execute one micro-live candidate (Coinbase market BUY) from candidate_queue.

    Safety:
    - Only runs when EZRA_LIVE_MICRO_ENABLED + COINBASE_EXECUTION_ENABLED + EZRA_LIVE_MICRO_AUTOTRADE_ENABLED.
    - live_order_guard and live_micro contract are enforced inside CoinbaseClient (guarded order POST).
    """
    root = Path(runtime_root).resolve()
    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)

    if not _truthy_env("EZRA_LIVE_MICRO_ENABLED"):
        return {"ok": True, "skipped": True, "reason": "micro_disabled"}
    if not _truthy_env("COINBASE_EXECUTION_ENABLED"):
        return {"ok": True, "skipped": True, "reason": "coinbase_execution_disabled"}
    if not _truthy_env("EZRA_LIVE_MICRO_AUTOTRADE_ENABLED"):
        return {"ok": True, "skipped": True, "reason": "autotrade_disabled"}

    # Contract gate (fail-closed).
    from trading_ai.deployment.live_micro_enablement import assert_live_micro_runtime_contract

    okc, err, audit = assert_live_micro_runtime_contract(root, phase="live_micro_candidate_execution")
    if not okc:
        return {"ok": False, "blocked": True, "reason": err, "audit": audit}

    from trading_ai.global_layer.review_storage import ReviewStorage

    st = ReviewStorage()
    cq = st.load_json("candidate_queue.json")
    it, idx = _pick_candidate_item(cq)
    if not it:
        return {"ok": True, "skipped": True, "reason": "no_candidates"}

    pid = str(it.get("product_id") or "").strip().upper()
    max_notional = _load_micro_max_notional(root)
    quote_usd = max(0.0, float(max_notional or 0.0))
    if quote_usd <= 0:
        return {"ok": False, "blocked": True, "reason": "missing_or_invalid_max_notional_usd"}

    events_p = root / "data" / "control" / "live_micro_execution_events.jsonl"
    _append_jsonl(
        events_p,
        {
            "ts": time.time(),
            "event": "candidate_selected",
            "product_id": pid,
            "gate_id": "gate_b",
            "candidate_item": it,
        },
    )
    items = list(cq.get("items") or [])
    if 0 <= idx < len(items) and isinstance(items[idx], dict):
        items[idx] = {**items[idx], "status": "selected", "selected_ts": time.time()}
    cq["items"] = items[-300:]
    st.save_json("candidate_queue.json", cq)

    from trading_ai.shark.outlets.coinbase import CoinbaseClient

    client = CoinbaseClient()
    res = client.place_market_buy(pid, quote_usd, execution_gate="gate_b")
    _append_jsonl(
        events_p,
        {
            "ts": time.time(),
            "event": "order_submitted",
            "product_id": pid,
            "gate_id": "gate_b",
            "order_result": {
                "success": bool(res.success),
                "status": res.status,
                "reason": res.reason,
                "order_id": res.order_id,
            },
        },
    )

    out: Dict[str, Any] = {"ok": True, "product_id": pid, "order_id": res.order_id, "submitted": bool(res.success)}
    if not res.success or not str(res.order_id or "").strip():
        # Mark queue item failed.
        cq2 = st.load_json("candidate_queue.json")
        its2 = list(cq2.get("items") or [])
        if 0 <= idx < len(its2) and isinstance(its2[idx], dict):
            its2[idx] = {**its2[idx], "status": "submit_failed", "submit_failed_ts": time.time(), "reason": res.reason}
        cq2["items"] = its2[-300:]
        st.save_json("candidate_queue.json", cq2)
        return {**out, "filled": False}

    # Best-effort fill probe (do not block the daemon loop).
    fills = []
    try:
        fills = client.get_fills(str(res.order_id))
    except Exception:
        fills = []
    filled = bool(fills)
    out["filled"] = filled
    _append_jsonl(
        events_p,
        {
            "ts": time.time(),
            "event": "fill_probe",
            "product_id": pid,
            "gate_id": "gate_b",
            "filled": filled,
            "fills_n": len(fills),
        },
    )

    if filled:
        trade = {
            "trade_id": str(res.order_id),
            "avenue_id": "A",
            "gate_id": "gate_b",
            "outlet": "coinbase",
            "market": pid,
            "product_id": pid,
            "live_or_paper": "live",
            "quote_usd": quote_usd,
            "status": "open",
        }
        try:
            from trading_ai.automation.post_trade_hub import execute_post_trade_placed

            tg = execute_post_trade_placed(None, trade)
            out["telegram_placed"] = tg.get("status")
        except Exception as exc:
            out["telegram_placed"] = f"error:{type(exc).__name__}"

        cq3 = st.load_json("candidate_queue.json")
        its3 = list(cq3.get("items") or [])
        if 0 <= idx < len(its3) and isinstance(its3[idx], dict):
            its3[idx] = {**its3[idx], "status": "filled", "filled_ts": time.time(), "order_id": str(res.order_id)}
        cq3["items"] = its3[-300:]
        st.save_json("candidate_queue.json", cq3)

    return out

