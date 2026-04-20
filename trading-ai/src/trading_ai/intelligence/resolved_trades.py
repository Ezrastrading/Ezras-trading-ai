"""Explicit trade resolution per truth scope — no silent mixing."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.global_layer.trade_truth import load_federated_trades
from trading_ai.intelligence.trade_row_normalize import normalize_trade_rows, trades_usable_for_windows
from trading_ai.intelligence.truth_contract import policy_for_review, policy_for_runtime
from trading_ai.nte.capital_ledger import load_ledger
from trading_ai.nte.databank.local_trade_store import DatabankRootUnsetError
from trading_ai.nte.memory.store import MemoryStore

logger = logging.getLogger(__name__)


def _nte_memory_trades(store: MemoryStore) -> List[Dict[str, Any]]:
    store.ensure_defaults()
    tm = store.load_json("trade_memory.json")
    return [t for t in (tm.get("trades") or []) if isinstance(t, dict)]


def resolve_for_runtime(store: Optional[MemoryStore] = None) -> Dict[str, Any]:
    """NTE trade_memory only — operational runtime truth."""
    st = store or MemoryStore()
    raw = _nte_memory_trades(st)
    normed, dq = normalize_trade_rows(raw)
    usable = trades_usable_for_windows(normed)
    return {
        "truth_version": "resolved_trades_v1",
        "source_policy_used": policy_for_runtime(),
        "rows_raw_count": len(raw),
        "rows_normalized": normed,
        "rows_for_windows": usable,
        "data_quality": dq,
        "federation_meta": {},
        "discrepancies_vs_federated": [],
    }


def resolve_for_review(store: Optional[MemoryStore] = None) -> Dict[str, Any]:
    """Federated trades (memory + databank) for review / packet intelligence."""
    st = store or MemoryStore()
    try:
        merged, meta = load_federated_trades(nte_store=st)
    except DatabankRootUnsetError as exc:
        merged = _nte_memory_trades(st)
        meta = {"fallback": "nte_memory_only", "databank": "root_unset", "error": str(exc)}
    except Exception as exc:
        logger.warning("federated load failed: %s — falling back to NTE memory only", exc)
        merged = _nte_memory_trades(st)
        meta = {"error": str(exc), "fallback": "nte_memory_only"}
    normed, dq = normalize_trade_rows(merged)
    usable = trades_usable_for_windows(normed)
    disc: List[Dict[str, Any]] = []
    if isinstance(meta, dict):
        fc = meta.get("federation_conflicts") or []
        if fc:
            disc.extend([{"kind": "federation_conflict", "detail": x} for x in fc[:50]])
    return {
        "truth_version": "resolved_trades_v1",
        "source_policy_used": policy_for_review(),
        "rows_raw_count": len(merged),
        "rows_normalized": normed,
        "rows_for_windows": usable,
        "data_quality": dq,
        "federation_meta": meta if isinstance(meta, dict) else {"packet": meta},
        "discrepancies_vs_federated": disc,
    }


def compare_ledger_to_trade_sum(normed_rows: List[Dict[str, Any]], *, ledger_path: Optional[Any] = None) -> Dict[str, Any]:
    """Capital cross-check: ledger realized vs sum of trade net (normalized where possible)."""
    led = load_ledger(ledger_path)
    realized = float(led.get("realized_pnl_net") or led.get("realized_pnl_usd") or 0.0)
    s = 0.0
    n = 0
    for t in normed_rows:
        v = t.get("_norm_net")
        if v is not None:
            s += float(v)
            n += 1
    delta = realized - s
    eps = 1.0
    return {
        "ledger_realized_pnl_net": realized,
        "sum_trade_net_pnl_closed_rows": s,
        "rows_in_sum": n,
        "delta_ledger_minus_sum": delta,
        "disagreement": abs(delta) > eps and n > 0,
        "honesty": "Disagreement can reflect timing, partial closes, or rows outside memory; investigate packet_truth.",
    }


def build_discrepancy_report(
    runtime_bundle: Dict[str, Any],
    review_bundle: Dict[str, Any],
) -> Dict[str, Any]:
    """Compare runtime vs review row counts and optional net sums."""
    rr = runtime_bundle.get("rows_for_windows") or []
    rv = review_bundle.get("rows_for_windows") or []
    return {
        "truth_version": "discrepancy_report_v1",
        "runtime_usable_rows": len(rr),
        "review_usable_rows": len(rv),
        "delta_review_minus_runtime": len(rv) - len(rr),
        "runtime_federation_meta_empty": not (runtime_bundle.get("federation_meta") or {}),
        "review_discrepancies": review_bundle.get("discrepancies_vs_federated") or [],
        "honesty": "Review federated count can exceed NTE-only when databank adds rows; conflicts listed in federation_meta.",
    }
