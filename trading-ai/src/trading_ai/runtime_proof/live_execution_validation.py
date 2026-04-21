"""
Blocker 2–4 and Phase 5: **operator-invoked** live execution validation and proof files.

Real capital: :func:`run_single_live_execution_validation` places and closes a **real** Coinbase
market order when all guards pass (including ``LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM``).
Automated agents must not call this without explicit operator intent.

Proof paths (under ``EZRAS_RUNTIME_ROOT``):

- ``execution_proof/live_execution_validation.json`` — final boolean matrix (Phase 5).
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest.mock import patch

from trading_ai.global_layer.governance_order_gate import check_new_order_allowed_full
from trading_ai.global_layer.realized_pnl import RealizedPnlResult, compute_realized_pnl
from trading_ai.global_layer.review_scheduler import run_full_review_cycle, tick_scheduler
from trading_ai.global_layer.review_storage import ReviewStorage
from trading_ai.global_layer.trade_truth import load_federated_trades
from trading_ai.nte.databank.local_trade_store import global_trade_events_path
from trading_ai.nte.databank.supabase_trade_sync import (
    report_supabase_trade_sync_diagnostics,
    select_trade_event_exists,
)
from trading_ai.nte.databank.trade_intelligence_databank import TradeIntelligenceDatabank
from trading_ai.nte.execution.product_rules import round_base_to_increment
from trading_ai.nte.memory.store import MemoryStore
from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.runtime_proof.coinbase_accounts import resolve_validation_market_product
from trading_ai.runtime_proof.coinbase_spot_fill_truth import (
    FlattenSizeValidationError,
    log_flatten_sizing,
    normalize_coinbase_buy_fills,
    normalize_coinbase_sell_fills,
    validate_flatten_base_before_sell,
)
from trading_ai.runtime_proof.live_first_20_operator import attach_governance_decision_log

logger = logging.getLogger(__name__)

LIVE_CONFIRM_ENV = "LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM"
LIVE_CONFIRM_VALUE = "YES_I_UNDERSTAND_REAL_CAPITAL"


def _truthy_env(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes")


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _poll_until_buy_filled(
    client: Any,
    order_id: str,
    *,
    timeout_sec: float = 120.0,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    deadline = time.monotonic() + timeout_sec
    last_order: Dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            last_order = client.get_order(order_id)
        except Exception as exc:
            logger.warning("get_order %s: %s", order_id, exc)
            last_order = {}
        fills = client.get_fills(order_id)
        if fills:
            return fills, last_order
        st = str(last_order.get("status") or "").lower()
        if st in ("filled", "done", "cancelled", "canceled", "expired"):
            if st in ("filled", "done") or fills:
                return fills, last_order
            break
        sleep_fn(0.4)
    fills = client.get_fills(order_id)
    return fills, last_order


def _poll_until_order_fill_rows(
    client: Any,
    order_id: str,
    *,
    product_id: str,
    side: str,
    timeout_sec: float = 120.0,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], str, List[str]]:
    """Prefer REST fills; fall back to a synthetic row from ``get_order`` when status is FILLED."""
    _ = product_id
    deadline = time.monotonic() + timeout_sec
    diag: List[str] = []
    last_snap: Dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            fills = client.get_fills(order_id)
        except Exception as exc:  # pragma: no cover
            fills = []
            diag.append(f"get_fills_error:{exc}")
        if fills:
            try:
                last_snap = client.get_order(order_id)
            except Exception:
                last_snap = {}
            return fills, last_snap, "fills_api", diag
        try:
            last_snap = client.get_order(order_id)
        except Exception as exc:  # pragma: no cover
            diag.append(f"get_order_error:{exc}")
            last_snap = {}
        st = str(last_snap.get("status") or "").upper()
        if st == "FILLED":
            synth = [
                {
                    "price": last_snap.get("average_filled_price"),
                    "size": last_snap.get("filled_size"),
                    "filled_value": last_snap.get("filled_value"),
                    "commission": last_snap.get("total_fees"),
                    "side": side,
                    "synthetic_order_snapshot": True,
                }
            ]
            diag.append("synthetic_from_order_snapshot")
            return synth, last_snap, "order_snapshot", diag
        sleep_fn(0.05)
    try:
        last_snap = client.get_order(order_id)
    except Exception:
        last_snap = {}
    try:
        fills = client.get_fills(order_id)
    except Exception:
        fills = []
    return fills, last_snap, "timeout", diag + ["timeout"]


def verify_data_pipeline_after_trade(
    runtime_root: Path,
    trade_id: str,
    *,
    process_stages: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Blocker 3 — verify local + federation + optional Supabase + governance log + review packet.

    Returns booleans and diagnostic strings only (no secrets).
    """
    runtime_root = runtime_root.resolve()
    trade_id = str(trade_id).strip()
    out: Dict[str, Any] = {
        "trade_memory_updated": False,
        "trade_events_appended": False,
        "federated_includes_trade_id": False,
        "supabase_upsert_true": False,
        "supabase_row_exists": False,
        "governance_log_has_entry": False,
        "review_packet_updated": False,
    }

    nte_mem = runtime_root / "shark" / "nte" / "memory"
    ms = MemoryStore(root=nte_mem)
    ms.ensure_defaults()
    tm = ms.load_json("trade_memory.json")
    trades = tm.get("trades") or []
    out["trade_memory_updated"] = any(str(t.get("trade_id")) == trade_id for t in trades if isinstance(t, dict))

    te = (runtime_root / "databank" / "trade_events.jsonl").resolve()
    out["trade_events_appended"] = False
    if te.is_file():
        try:
            for line in reversed(te.read_text(encoding="utf-8").splitlines()):
                s = line.strip()
                if not s:
                    continue
                try:
                    rec = json.loads(s)
                except Exception:
                    if trade_id in s:
                        out["trade_events_appended"] = True
                    continue
                if str(rec.get("trade_id") or "").strip() == trade_id:
                    out["trade_events_appended"] = True
                    break
        except Exception:
            tail = te.read_text(encoding="utf-8")[-8000:]
            out["trade_events_appended"] = trade_id in tail

    fed, _meta = load_federated_trades(nte_store=ms)
    out["federated_includes_trade_id"] = any(str(r.get("trade_id")) == trade_id for r in fed)

    glog = runtime_root / "governance_gate_decisions.log"
    if glog.is_file():
        tail_g = glog.read_text(encoding="utf-8")[-12000:]
        out["governance_log_has_entry"] = "governance_gate_decision" in tail_g

    pkt_path = runtime_root / "shark" / "memory" / "global" / "review_packet_latest.json"
    out["review_packet_updated"] = pkt_path.is_file() and pkt_path.stat().st_size > 2

    stg = process_stages or {}
    out["supabase_upsert_true"] = bool(stg.get("supabase_trade_events"))
    if out["supabase_upsert_true"]:
        out["supabase_row_exists"] = select_trade_event_exists(trade_id)

    return out


def run_short_runtime_stability(
    runtime_root: Path,
    *,
    duration_sec: float = 150.0,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Dict[str, Any]:
    """
    Blocker 4 — run ``tick_scheduler`` for ``duration_sec`` with ``run_full_review_cycle(..., skip_models=True)``.
    """
    runtime_root = runtime_root.resolve()
    os.environ["EZRAS_RUNTIME_ROOT"] = str(runtime_root)
    os.environ.setdefault("GOVERNANCE_ORDER_ENFORCEMENT", "true")

    st = ReviewStorage()
    st.ensure_review_files()

    _orig_cycle = run_full_review_cycle

    def _wrap_cycle(*a: Any, **kw: Any) -> Any:
        kw["skip_models"] = True
        return _orig_cycle(*a, **kw)

    tick_path = st.store.path("review_scheduler_ticks.jsonl")
    start_lines = 0
    if tick_path.is_file():
        start_lines = len([x for x in tick_path.read_text(encoding="utf-8").splitlines() if x.strip()])

    t_end = time.monotonic() + duration_sec
    errors: List[str] = []
    with patch("trading_ai.global_layer.review_scheduler.run_full_review_cycle", side_effect=_wrap_cycle):
        while time.monotonic() < t_end:
            try:
                tick_scheduler(storage=st)
            except Exception as exc:
                errors.append(f"tick_scheduler:{exc!s}")
            sleep_fn(2.0)

    ok_parse = True
    ok_pairs = True
    if tick_path.is_file():
        new_lines = [ln for ln in tick_path.read_text(encoding="utf-8").splitlines() if ln.strip()][start_lines:]
        eval_n = complete_n = 0
        for ln in new_lines:
            try:
                rec = json.loads(ln)
            except json.JSONDecodeError:
                ok_parse = False
                continue
            ph = rec.get("phase")
            if ph == "tick_evaluate":
                eval_n += 1
            elif ph == "tick_complete":
                complete_n += 1
        if eval_n != complete_n:
            ok_pairs = False

    return {
        "scheduler_stable": len(errors) == 0 and ok_parse and ok_pairs,
        "parse_ok": ok_parse,
        "evaluate_complete_paired": ok_pairs,
        "errors": errors,
        "duration_sec": duration_sec,
        "tick_path": str(tick_path),
    }


def _pnl_calculation_verified(pnl_res: RealizedPnlResult, round_trip_complete: bool) -> bool:
    """True when spot realized PnL was computed from complete buy/sell/fee inputs."""
    if not round_trip_complete:
        return False
    if pnl_res.net_pnl is None:
        return False
    notes = list(pnl_res.notes or [])
    if "incomplete_spot_inputs" in notes:
        return False
    return True


def _collect_partial_failure_codes(
    sched: Dict[str, Any],
    *,
    sell_success: bool,
    sell_fills: List[Dict[str, Any]],
    databank_ok: bool,
    round_trip_complete: bool,
) -> List[str]:
    """
    Any sub-system fault that is not acceptable for First 20 readiness
    (stability ticks, incomplete round-trip, databank, sell leg).
    """
    codes: List[str] = []
    if not databank_ok:
        codes.append("databank_process_failed")
    if not round_trip_complete:
        codes.append("round_trip_incomplete")
    if not sell_success or not sell_fills:
        codes.append("sell_leg_incomplete_or_failed")
    errs = sched.get("errors") or []
    if errs:
        codes.append("runtime_stability_tick_errors")
    if not bool(sched.get("scheduler_stable")):
        codes.append("runtime_stability_unstable")
    if not bool(sched.get("parse_ok", True)):
        codes.append("runtime_stability_parse_unhealthy")
    if not bool(sched.get("evaluate_complete_paired", True)):
        codes.append("runtime_stability_eval_complete_mismatch")
    return sorted(set(codes))


def evaluate_ready_for_first_20(
    *,
    execution_success: bool,
    supabase_synced: bool,
    governance_logged: bool,
    pnl_res: RealizedPnlResult,
    round_trip_complete: bool,
    partial_failure_codes: List[str],
) -> Tuple[bool, List[str], bool]:
    """
    READY_FOR_FIRST_20 is True only when all gates pass and there are no partial failures.

    Returns ``(ready, failure_reasons, pnl_calculation_verified)``.
    """
    pnl_verified = _pnl_calculation_verified(pnl_res, round_trip_complete)
    no_partial = len(partial_failure_codes) == 0
    ready = (
        execution_success
        and supabase_synced
        and governance_logged
        and pnl_verified
        and no_partial
    )
    reasons: List[str] = []
    if not execution_success:
        reasons.append("execution_success_false")
    if not supabase_synced:
        reasons.append("supabase_synced_false")
    if not governance_logged:
        reasons.append("governance_logged_false")
    if not pnl_verified:
        reasons.append("pnl_calculation_not_verified")
    if not no_partial:
        reasons.append("partial_failures:" + ",".join(partial_failure_codes))
    if ready:
        return True, [], pnl_verified
    return False, reasons, pnl_verified


def write_execution_proof_json(runtime_root: Path, payload: Dict[str, Any]) -> Path:
    root = runtime_root.resolve()
    out_dir = root / "execution_proof"
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / "live_execution_validation.json"
    p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return p


def _proof_failure_payload(**extra: Any) -> Dict[str, Any]:
    base = {
        "execution_success": False,
        "coinbase_order_verified": False,
        "databank_written": False,
        "supabase_synced": False,
        "governance_logged": False,
        "packet_updated": False,
        "scheduler_stable": False,
        "FINAL_EXECUTION_PROVEN": False,
        "READY_FOR_FIRST_20": False,
    }
    base.update(extra)
    return base


def run_single_live_execution_validation(
    runtime_root: Optional[Path] = None,
    *,
    quote_usd: float = 10.0,
    product_id: str = "BTC-USD",
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Blocker 2 — one real round-trip: market BUY (``quote_usd``) then SELL to flat.

    **Guards** (all required):

    - ``LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM=YES_I_UNDERSTAND_REAL_CAPITAL``
    - ``COINBASE_EXECUTION_ENABLED=true``
    - ``EZRAS_DRY_RUN`` unset or false
    - Joint governance must allow (healthy ``joint_review_latest.json`` under runtime root)

    Returns a dict including pipeline verification and paths; on guard failure **no order** is sent.

    Extra keyword arguments (e.g. ``include_runtime_stability``, ``execution_profile``) are accepted
    for backward compatibility with Avenue A daemon / profit-cycle callers and ignored here.
    """
    _ = kwargs  # reserved for profit-cycle alignment; Gate A round-trip path is unchanged.
    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)

    base_out: Dict[str, Any] = {
        "execution_success": False,
        "coinbase_order_verified": False,
        "databank_written": False,
        "supabase_synced": False,
        "governance_logged": False,
        "packet_updated": False,
        "scheduler_stable": False,
        "error": None,
        "runtime_root": str(root),
    }

    if (os.environ.get(LIVE_CONFIRM_ENV) or "").strip() != LIVE_CONFIRM_VALUE:
        base_out["error"] = f"missing_or_invalid_{LIVE_CONFIRM_ENV}"
        write_execution_proof_json(root, _proof_failure_payload(error=base_out["error"]))
        return base_out

    if not _truthy_env("COINBASE_EXECUTION_ENABLED"):
        base_out["error"] = "COINBASE_EXECUTION_ENABLED_not_true"
        write_execution_proof_json(root, _proof_failure_payload(error=base_out["error"]))
        return base_out

    if _truthy_env("EZRAS_DRY_RUN"):
        base_out["error"] = "EZRAS_DRY_RUN_blocks_live_validation"
        write_execution_proof_json(root, _proof_failure_payload(error=base_out["error"]))
        return base_out

    attach_governance_decision_log(root)

    gov_ok, gov_reason, _ = check_new_order_allowed_full(
        venue="coinbase",
        operation="live_execution_validation",
        route="live_execution_validation",
        intent_id="pre_live_validation",
        log_decision=True,
    )
    if not gov_ok:
        base_out["error"] = f"governance_blocked:{gov_reason}"
        write_execution_proof_json(root, _proof_failure_payload(error=base_out["error"]))
        return base_out

    base_out["supabase_sync_diagnostics"] = report_supabase_trade_sync_diagnostics()

    from trading_ai.shark.outlets.coinbase import CoinbaseClient

    client = CoinbaseClient()
    chosen_product, quote_diag, quote_err = resolve_validation_market_product(
        client,
        quote_notional=float(quote_usd),
        preferred_product_id=product_id,
    )
    if quote_err:
        base_out["error"] = f"quote_precheck_failed:{quote_err}"
        base_out["quote_diagnostics"] = quote_diag
        write_execution_proof_json(root, _proof_failure_payload(error=base_out["error"], quote_diagnostics=quote_diag))
        return base_out

    product_id = chosen_product
    trade_id = f"live_exec_{uuid.uuid4().hex[:12]}"
    t_req = time.monotonic()

    buy = client.place_market_buy(product_id, float(quote_usd))
    if not buy.success or not (buy.order_id or "").strip():
        base_out["error"] = f"buy_failed:{buy.reason or buy.status}"
        write_execution_proof_json(root, _proof_failure_payload(error=base_out["error"]))
        return base_out

    oid = str(buy.order_id).strip()
    fills, _order_snap = _poll_until_buy_filled(client, oid)
    if not fills:
        base_out["error"] = "buy_not_filled_in_time"
        base_out["buy_order_id"] = oid
        write_execution_proof_json(
            root, _proof_failure_payload(error=base_out["error"], buy_order_id=oid)
        )
        return base_out

    t_fill = time.monotonic()
    latency_ms = (t_fill - t_req) * 1000.0
    buy_agg = normalize_coinbase_buy_fills(product_id, fills)
    quote_buy = buy_agg.buy_quote_spent
    base_size = buy_agg.buy_base_qty
    fee_buy = buy_agg.fees_buy_usd
    avg_px = buy_agg.avg_fill_price

    base_s = round_base_to_increment(product_id, base_size)
    try:
        validate_flatten_base_before_sell(
            product_id=product_id,
            raw_base_qty_bought=base_size,
            rounded_base_str=base_s,
            buy_quote_spent=quote_buy,
            ref_price_usd_per_base=avg_px if avg_px > 0 else 0.0,
            quote_notional_request=float(quote_usd),
        )
    except FlattenSizeValidationError as exc:
        base_out["error"] = str(exc)
        base_out["buy_fill_truth"] = {
            "buy_base_qty": base_size,
            "buy_quote_spent": quote_buy,
            "avg_fill_price": avg_px,
            "normalized_notes": buy_agg.confidence_notes,
        }
        write_execution_proof_json(
            root,
            _proof_failure_payload(
                error=base_out["error"],
                buy_order_id=oid,
                buy_fill_truth=base_out["buy_fill_truth"],
            ),
        )
        return base_out

    log_flatten_sizing(
        product_id=product_id,
        raw_base_qty=base_size,
        rounded_base_str=base_s,
        ref_price_usd_per_base=avg_px,
        buy_quote_spent=quote_buy,
    )

    sell = client.place_market_sell(product_id, base_s)
    sell_id = str(sell.order_id or "").strip()
    sell_fills: List[Dict[str, Any]] = []
    if sell.success and sell_id:
        sell_fills, _ = _poll_until_buy_filled(client, sell_id, timeout_sec=120.0)
    if sell_fills:
        sell_agg = normalize_coinbase_sell_fills(product_id, sell_fills)
    else:
        sell_agg = None
    quote_sell = sell_agg.sell_quote_received if sell_agg else None
    fee_sell = sell_agg.fees_sell_usd if sell_agg else None

    try:
        if fee_sell is not None:
            fees_total = float(fee_buy) + float(fee_sell)
        else:
            fees_total = float(fee_buy)
    except (TypeError, ValueError):
        fees_total = fee_buy if fee_sell is None else fee_buy + fee_sell

    round_trip_complete = bool(sell_fills) and sell.success and quote_buy > 0
    pnl_res = compute_realized_pnl(
        {
            "instrument_kind": "spot",
            "buy_quote_spent": quote_buy,
            "sell_quote_received": quote_sell if round_trip_complete else None,
            "fees_total": fees_total if round_trip_complete else None,
            "fields_complete": round_trip_complete,
        }
    )
    pnl = pnl_res.net_pnl
    gross_for_record = pnl_res.gross_pnl
    slip = 0.0
    if avg_px > 0 and fills:
        slip = abs(float(fills[0].get("price") or avg_px) - avg_px) / avg_px

    now = time.time()
    raw: Dict[str, Any] = {
        "trade_id": trade_id,
        "avenue_id": "A",
        "avenue_name": "coinbase",
        "asset": product_id,
        "strategy_id": "live_execution_validation",
        "route_chosen": "A",
        "regime": "validation",
        "timestamp_open": _iso(t_req),
        "timestamp_close": _iso(now),
        "expected_edge_bps": 0.0,
        "instrument_kind": "spot",
        "buy_quote_spent": float(quote_buy),
        "buy_base_qty": float(base_size),
        "sell_base_qty": float(sell_agg.base_sold) if sell_agg else None,
        "sell_quote_received": float(quote_sell) if quote_sell is not None else None,
        "net_pnl": float(pnl) if pnl is not None else None,
        "gross_pnl": float(gross_for_record) if gross_for_record is not None else None,
        "fees_paid": float(fees_total) if round_trip_complete else float(fee_buy),
        "realized_pnl_complete": round_trip_complete,
        "pnl_sign": pnl_res.pnl_sign,
        "return_bps": float(pnl_res.return_bps) if pnl_res.return_bps is not None else None,
        "exit_reason": "live_validation_flat",
        "maker_taker": "taker",
        "fill_truth_notes": buy_agg.confidence_notes + (sell_agg.confidence_notes if sell_agg else []),
    }

    ms = MemoryStore()
    ms.ensure_defaults()
    ms.append_trade(
        {
            "trade_id": trade_id,
            "avenue": "coinbase",
            "product_id": product_id,
            "net_pnl_usd": float(pnl) if pnl is not None else None,
            "strategy_class": "live_execution_validation",
            "execution_latency_ms": latency_ms,
            "notes": "live_execution_validation",
        }
    )

    tidb = TradeIntelligenceDatabank()
    proc = tidb.process_closed_trade(raw)
    databank_ok = bool(proc.get("ok"))

    st = ReviewStorage()
    st.ensure_review_files()
    try:
        run_full_review_cycle("midday", storage=st, skip_models=True)
    except Exception as exc:
        logger.warning("review_cycle_after_validation: %s", exc)

    stages = proc.get("stages") if isinstance(proc.get("stages"), dict) else {}
    pipe = verify_data_pipeline_after_trade(root, trade_id, process_stages=stages)

    sched = run_short_runtime_stability(root, duration_sec=150.0)

    coinbase_ok = bool(sell.success and sell_fills)
    base_out["flatten_sizing"] = {
        "raw_base_qty": base_size,
        "rounded_base_str": base_s,
        "ref_price_usd_per_base": avg_px,
        "buy_quote_spent": quote_buy,
    }
    base_out["realized_pnl"] = {
        "instrument_kind": "spot",
        "buy_quote_spent": quote_buy,
        "sell_quote_received": quote_sell if round_trip_complete else None,
        "total_fees": fees_total if round_trip_complete else None,
        "gross_pnl": gross_for_record,
        "net_pnl": pnl,
        "pnl_sign": pnl_res.pnl_sign,
        "return_bps": pnl_res.return_bps,
        "complete": round_trip_complete,
    }
    base_out.update(
        {
            "execution_success": bool(
                databank_ok
                and coinbase_ok
                and pipe.get("trade_memory_updated")
                and pipe.get("trade_events_appended")
                and pipe.get("federated_includes_trade_id")
            ),
            "coinbase_order_verified": coinbase_ok,
            "databank_written": databank_ok,
            "supabase_synced": bool(pipe.get("supabase_upsert_true") and pipe.get("supabase_row_exists")),
            "governance_logged": pipe.get("governance_log_has_entry", False),
            "packet_updated": pipe.get("review_packet_updated", False),
            "scheduler_stable": bool(sched.get("scheduler_stable")),
            "order_id_buy": oid,
            "order_id_sell": sell_id,
            "fill_price_avg": avg_px,
            "fill_time_iso": _iso(t_fill),
            "fees_usd": fees_total if round_trip_complete else None,
            "slippage_estimate": slip,
            "execution_latency_ms": latency_ms,
            "pipeline": pipe,
            "databank_process_closed": proc,
            "stability": sched,
        }
    )

    all_ok = (
        base_out["execution_success"]
        and base_out["coinbase_order_verified"]
        and base_out["databank_written"]
        and base_out["supabase_synced"]
        and base_out["governance_logged"]
        and base_out["packet_updated"]
        and base_out["scheduler_stable"]
    )
    base_out["all_blockers_green"] = all_ok

    partial_codes = _collect_partial_failure_codes(
        sched,
        sell_success=bool(sell.success),
        sell_fills=sell_fills,
        databank_ok=databank_ok,
        round_trip_complete=round_trip_complete,
    )
    ready_first20, ready_fail_reasons, pnl_verified = evaluate_ready_for_first_20(
        execution_success=bool(base_out["execution_success"]),
        supabase_synced=bool(base_out["supabase_synced"]),
        governance_logged=bool(base_out["governance_logged"]),
        pnl_res=pnl_res,
        round_trip_complete=round_trip_complete,
        partial_failure_codes=partial_codes,
    )
    base_out["pnl_calculation_verified"] = pnl_verified
    base_out["partial_failure_codes"] = partial_codes
    base_out["READY_FOR_FIRST_20"] = ready_first20
    base_out["ready_for_first_20_failure_reasons"] = ready_fail_reasons

    if not ready_first20:
        logger.warning(
            "READY_FOR_FIRST_20=false — %s",
            "; ".join(ready_fail_reasons) if ready_fail_reasons else "unspecified",
        )

    proof = {k: base_out[k] for k in (
        "execution_success",
        "coinbase_order_verified",
        "databank_written",
        "supabase_synced",
        "governance_logged",
        "packet_updated",
        "scheduler_stable",
    )}
    proof["FINAL_EXECUTION_PROVEN"] = all_ok
    proof["READY_FOR_FIRST_20"] = ready_first20
    proof["pnl_calculation_verified"] = pnl_verified
    proof["partial_failure_codes"] = partial_codes
    proof["ready_for_first_20_failure_reasons"] = ready_fail_reasons
    proof["supabase_sync_diagnostics"] = base_out.get("supabase_sync_diagnostics") or {}
    proof["flatten_sizing"] = base_out.get("flatten_sizing") or {}
    proof["realized_pnl"] = base_out.get("realized_pnl") or {}
    base_out["proof"] = proof
    base_out["FINAL_EXECUTION_PROVEN"] = all_ok

    write_execution_proof_json(root, proof)
    return base_out


def _supabase_row_exists_with_retry(*_args: Any, **_kwargs: Any) -> bool:
    """Test seam for Supabase existence checks (patched in unit tests)."""
    return False


def _gate_a_operator_confirms_live_round_trip(runtime_root: Path | str) -> bool:
    """True when autonomous ack file is confirmed and Avenue A daemon is marked active in the environment."""
    root = Path(runtime_root)
    ack = root / "data" / "control" / "avenue_a_autonomous_live_ack.json"
    if not ack.is_file():
        return False
    try:
        data = json.loads(ack.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(data, dict) or data.get("confirmed") is not True:
        return False
    scope = str(data.get("scope") or "")
    if "avenue_a" not in scope.lower():
        return False
    env_active = (os.environ.get("EZRAS_AVENUE_A_DAEMON_ACTIVE") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    return bool(env_active)


def persist_successful_gate_a_proof_to_disk(runtime_root: Path | str, validation_out: Dict[str, Any]) -> None:
    """Overwrite ``execution_proof/live_execution_validation.json`` with a successful Gate A proof."""
    root = Path(runtime_root)
    ep = root / "execution_proof"
    ep.mkdir(parents=True, exist_ok=True)
    out: Dict[str, Any] = dict(validation_out)
    proof = out.get("proof")
    if isinstance(proof, dict):
        for k in ("FINAL_EXECUTION_PROVEN", "execution_success"):
            if k in proof and k not in out:
                out[k] = proof[k]
    out["FINAL_EXECUTION_PROVEN"] = bool(out.get("FINAL_EXECUTION_PROVEN", False))
    out["execution_success"] = bool(out.get("execution_success", False))
    out["error"] = None
    out["failure_code"] = None
    out["failure_reason"] = None
    out["runtime_root"] = str(root.resolve())
    (ep / "live_execution_validation.json").write_text(
        json.dumps(out, indent=2, sort_keys=False),
        encoding="utf-8",
    )


def run_gate_b_live_micro_validation(
    quote_usd: float = 10.0,
    product_id: str = "BTC-USD",
    *,
    include_runtime_stability: bool = True,
    runtime_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Operator CLI entry: Gate B Coinbase micro round-trip (writes ``execution_proof/gate_b_live_execution_validation.json``).

    Delegates to :func:`run_avenue_a_profit_cycle` with ``execution_profile="gate_b"``.
    """
    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)
    from trading_ai.orchestration.avenue_a_profit_cycle import run_avenue_a_profit_cycle

    return run_avenue_a_profit_cycle(
        root,
        quote_usd=float(quote_usd),
        product_id=str(product_id),
        include_runtime_stability=bool(include_runtime_stability),
        execution_profile="gate_b",
        gate_a_anchored_majors_only=True,
        avenue_a_autonomous_lane_decision=None,
    )


def duplicate_guard_proof_fields_for_live_validation() -> Dict[str, Any]:
    """Fields duplicated into live-validation proof JSON for duplicate-window audits."""
    from trading_ai.nte.hardening.live_order_guard import deployment_micro_validation_duplicate_isolation_key

    active = (os.environ.get("EZRAS_DEPLOYMENT_MICRO_VALIDATION_ACTIVE") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    iso = deployment_micro_validation_duplicate_isolation_key() if active else None
    return {
        "duplicate_guard_mode": (
            "deployment_micro_validation_isolated_keys" if active else "standard"
        ),
        "validation_scope_duplicate_isolation_key": iso,
        "duplicate_guard_bypassed_for_validation": False,
    }
