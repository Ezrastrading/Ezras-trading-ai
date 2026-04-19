"""
Canonical trade list for governance / AI packets: federated read with explicit precedence.

**Precedence (closed-trade truth for review):**
1. **NTE ``trade_memory.json``** — operational source of truth for NTE closes (always loaded).
2. **Trade Intelligence databank** (``trade_events.jsonl``) — append-only enrichment; duplicate ``trade_id``
   lines in the file are merged by **last row wins** with a ``duplicate_databank_line_count`` marker.

**Conflict resolution**
- **Net PnL / fees:** If memory and databank both supply numeric values and they differ beyond a small epsilon,
  memory wins for the merged row; ``truth_provenance.conflicts`` records the dispute (never silent overwrite).
- **Slippage:** Prefer non-null databank slippage fields when memory lacks them; never coerce missing to 0.0
  in ``truth_provenance`` — use ``fees_unknown`` / ``slippage_unknown`` flags when neither side has values.

**Double counting:** Merged output is one row per ``trade_id`` from memory (deduped last-wins) plus databank-only
rows not in memory — aggregation must not count the same ``trade_id`` twice.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EPS = 1e-6


def _tid(tr: Dict[str, Any]) -> str:
    return str(tr.get("trade_id") or tr.get("id") or "").strip()


def _db_tid(row: Dict[str, Any]) -> str:
    return str(row.get("trade_id") or "").strip()


def _fnet(t: Dict[str, Any]) -> Optional[float]:
    for k in ("net_pnl_usd", "net_pnl"):
        v = t.get(k)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _ffees(t: Dict[str, Any]) -> Optional[float]:
    for k in ("fees_usd", "fees", "fees_paid"):
        v = t.get(k)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _dedupe_memory_trades(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Last occurrence wins per trade_id; rows without id stay in order (rare)."""
    by_id: Dict[str, Dict[str, Any]] = {}
    no_id: List[Dict[str, Any]] = []
    for t in rows:
        tid = _tid(t)
        if not tid:
            no_id.append(dict(t))
            continue
        by_id[tid] = dict(t)
    out = list(by_id.values())
    out.extend(no_id)
    return out


def _index_databank_rows(
    db_rows: List[Dict[str, Any]],
) -> Tuple[Dict[str, List[Dict[str, Any]]], int, int]:
    """Group by trade_id; count trade_ids with duplicates and extra JSONL lines."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in db_rows:
        if not isinstance(row, dict):
            continue
        tid = _db_tid(row)
        if not tid:
            continue
        groups.setdefault(tid, []).append(row)
    dup_ids = sum(1 for g in groups.values() if len(g) > 1)
    extra_lines = sum(len(g) - 1 for g in groups.values() if len(g) > 1)
    return groups, dup_ids, extra_lines


def _pick_richest_row(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Prefer row with most non-null optional fields."""

    def score(r: Dict[str, Any]) -> int:
        keys = (
            "net_pnl",
            "fees_paid",
            "entry_slippage_bps",
            "exit_slippage_bps",
            "execution_score",
            "gross_pnl",
        )
        return sum(1 for k in keys if r.get(k) is not None)

    return max(rows, key=lambda r: score(r))


def load_federated_trades(
    *,
    nte_store: Optional[Any] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Returns (merged_trades, packet_truth_meta).

    ``packet_truth_meta`` includes counts, warnings, federation_conflicts, avenue coverage.
    """
    from trading_ai.global_layer.avenue_truth_contract import (
        build_representation_status,
        expected_truth_avenues,
        label_play_money,
        normalize_avenue_key,
    )
    from trading_ai.nte.memory.store import MemoryStore

    nte = nte_store or MemoryStore()
    nte.ensure_defaults()
    tm = nte.load_json("trade_memory.json")
    mem_raw: List[Dict[str, Any]] = [t for t in (tm.get("trades") or []) if isinstance(t, dict)]
    mem_trades = _dedupe_memory_trades(mem_raw)

    db_rows: List[Dict[str, Any]] = []
    db_root: Optional[Path] = None
    db_src: Optional[str] = None
    try:
        from trading_ai.nte.databank.local_trade_store import DatabankRootUnsetError, load_all_trade_events, resolve_databank_root

        db_root, db_src = resolve_databank_root()
    except DatabankRootUnsetError:
        raise
    try:
        db_rows = load_all_trade_events()
    except Exception as exc:
        logger.warning("trade_truth databank load: %s", exc)

    db_groups, databank_dup_trade_ids, databank_extra_lines = _index_databank_rows(db_rows)
    mem_ids = {_tid(t) for t in mem_trades if _tid(t)}

    federation_conflicts: List[Dict[str, Any]] = []
    merged: List[Dict[str, Any]] = []

    for t in mem_trades:
        tid = _tid(t)
        base = dict(t)
        prov: Dict[str, Any] = {"primary": "nte_trade_memory", "avenue_normalized": normalize_avenue_key(
            base.get("avenue") or base.get("avenue_name") or base.get("avenue_id")
        )}
        base["truth_provenance"] = prov
        if label_play_money(prov["avenue_normalized"]):
            base["unit"] = "play_money"
            prov["unit"] = "play_money"

        if tid and tid in db_groups:
            rows = db_groups[tid]
            ov = _pick_richest_row(rows)
            if len(rows) > 1:
                prov["duplicate_databank_line_count"] = len(rows)
            prov["enriched_from"] = "databank"
            m_net = _fnet(base)
            d_net = _fnet(ov)
            if m_net is not None and d_net is not None and abs(m_net - d_net) > _EPS * max(1.0, abs(m_net)):
                rec = {
                    "trade_id": tid,
                    "field": "net_pnl",
                    "memory": m_net,
                    "databank": d_net,
                    "resolution": "memory_wins",
                }
                federation_conflicts.append(rec)
                prov.setdefault("conflict_records", []).append(rec)
            m_fee = _ffees(base)
            d_fee = _ffees(ov)
            if m_fee is not None and d_fee is not None and abs(m_fee - d_fee) > _EPS * max(1.0, abs(m_fee)):
                rec = {
                    "trade_id": tid,
                    "field": "fees",
                    "memory": m_fee,
                    "databank": d_fee,
                    "resolution": "memory_wins",
                }
                federation_conflicts.append(rec)
                prov.setdefault("conflict_records", []).append(rec)
            for k in (
                "entry_slippage_bps",
                "exit_slippage_bps",
                "fees_paid",
                "execution_score",
                "trade_quality_score",
                "expected_net_edge_bps",
            ):
                if base.get(k) is None and ov.get(k) is not None:
                    base[k] = ov[k]
                    prov.setdefault("late_enrichment_from", "databank")
            if _ffees(base) is None and _ffees(ov) is not None:
                base["fees_paid"] = ov.get("fees_paid")
                prov["fees_source"] = "databank_fill"
        elif tid:
            prov["databank_match"] = False

        if _ffees(base) is None and _fnet(base) is not None:
            prov["fees_unknown"] = True
        slip_keys = ("entry_slippage_bps", "exit_slippage_bps", "realized_move_bps")
        if all(base.get(k) is None for k in slip_keys):
            prov["slippage_unknown"] = True

        merged.append(base)

    db_only = 0
    for tid, rows in db_groups.items():
        if tid in mem_ids:
            continue
        ov = _pick_richest_row(rows)
        r = dict(ov)
        r["truth_provenance"] = {
            "primary": "databank_only",
            "databank_only": True,
            "avenue_normalized": normalize_avenue_key(r.get("avenue_name") or r.get("avenue_id")),
        }
        if len(rows) > 1:
            r["truth_provenance"]["duplicate_databank_line_count"] = len(rows)
        r.setdefault("avenue", str(r.get("avenue_name") or r.get("avenue_id") or "unknown"))
        if r.get("net_pnl_usd") is None and r.get("net_pnl") is not None:
            r["net_pnl_usd"] = r.get("net_pnl")
        if label_play_money(r["truth_provenance"]["avenue_normalized"]):
            r["unit"] = "play_money"
        merged.append(r)
        db_only += 1

    merged_ids = {_tid(t) for t in merged if _tid(t)}
    mirror_only = 0
    try:
        from trading_ai.global_layer.kalshi_execution_mirror import load_mirror_rows

        for mw in load_mirror_rows():
            if not isinstance(mw, dict):
                continue
            tid = _tid(mw)
            if not tid or tid in merged_ids:
                continue
            r = dict(mw)
            r["truth_provenance"] = {
                "primary": "kalshi_execution_mirror",
                "execution_mirror": True,
                "avenue_normalized": normalize_avenue_key(
                    r.get("avenue") or r.get("avenue_name") or "kalshi"
                ),
                "truth_note": r.get("truth_note") or "execution_mirror_only_not_a_close",
            }
            r.setdefault("avenue", "kalshi")
            if label_play_money(r["truth_provenance"]["avenue_normalized"]):
                r["unit"] = "play_money"
            merged.append(r)
            merged_ids.add(tid)
            mirror_only += 1
    except Exception as exc:
        logger.debug("trade_truth kalshi mirror merge: %s", exc)

    warnings: List[str] = []
    if db_only > 0:
        warnings.append(
            f"{db_only} trade(s) databank-only — not in NTE trade_memory (e.g. Kalshi pipeline or legacy rows)."
        )
    if not db_rows and mem_trades:
        warnings.append("Databank trade_events.jsonl empty or unread — federated list uses NTE memory only.")
    if databank_dup_trade_ids:
        warnings.append(
            f"Databank: {databank_dup_trade_ids} trade_id(s) had duplicate JSONL lines "
            f"({databank_extra_lines} extra lines); merged richest row per id."
        )
    if federation_conflicts:
        warnings.append(f"Federation conflicts recorded: {len(federation_conflicts)} field disagreement(s).")
    if mirror_only > 0:
        warnings.append(
            f"{mirror_only} Kalshi execution mirror row(s) ingested — activity visibility only; "
            "not substitute for closed-trade PnL in databank/memory."
        )

    by_av = avenue_fairness_rollups(merged)["by_avenue"]
    exp = expected_truth_avenues()
    representation = build_representation_status(by_avenue_counts=by_av, expected=exp)
    for av, row in representation.items():
        if av == "kalshi" and row.get("representation") == "missing" and "kalshi" in exp:
            warnings.append(
                "Kalshi is expected active but shows zero federated closes — truth layer may be incomplete; "
                "do not treat as proof of zero Kalshi activity."
            )

    meta: Dict[str, Any] = {
        "model": "federated_nte_memory_plus_databank",
        "databank_root": str(db_root) if db_root is not None else None,
        "databank_root_source": db_src,
        "precedence": "memory_wins_on_value_conflict_databank_fills_nulls",
        "nte_memory_trade_count": len(mem_raw),
        "nte_memory_unique_trade_id_count": len(mem_ids),
        "databank_event_count": len(db_rows),
        "databank_duplicate_trade_id_count": databank_dup_trade_ids,
        "databank_extra_duplicate_lines": databank_extra_lines,
        "merged_trade_count": len(merged),
        "databank_only_trade_count": db_only,
        "kalshi_execution_mirror_only_count": mirror_only,
        "federation_conflict_count": len(federation_conflicts),
        "federation_conflicts": federation_conflicts[:50],
        "warnings": warnings,
        "fairness_warnings": list(warnings),
        "expected_avenues": sorted(exp),
        "avenue_representation": representation,
        "play_money_labeled": True,
        "read_at_ts": time.time(),
    }
    return merged, meta


def avenue_fairness_rollups(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per-avenue counts, PnL, data-quality hints."""
    from trading_ai.global_layer.avenue_truth_contract import normalize_avenue_key

    by_av: Dict[str, Dict[str, Any]] = {}
    for t in trades:
        raw_av = t.get("avenue") or t.get("avenue_name") or t.get("avenue_id") or "unknown"
        av = normalize_avenue_key(raw_av)
        if av not in by_av:
            by_av[av] = {
                "trade_count": 0,
                "closed_trade_count": 0,
                "net_pnl_usd": 0.0,
                "wins": 0,
                "unknown_net_count": 0,
                "fees_known_count": 0,
                "slippage_known_count": 0,
                "representation_quality_score": 100.0,
                "hard_stop_exit_count": 0,
                "trades_with_anomaly_flags": 0,
                "play_money_trade_count": 0,
                "usd_labeled_trade_count": 0,
            }
        row = by_av[av]
        row["trade_count"] += 1
        row["closed_trade_count"] += 1
        net = t.get("net_pnl_usd")
        if net is None:
            net = t.get("net_pnl")
        try:
            nf = float(net) if net is not None else None
        except (TypeError, ValueError):
            nf = None
        if nf is None:
            row["unknown_net_count"] += 1
        else:
            row["net_pnl_usd"] += nf
            if nf > 0:
                row["wins"] += 1
        prov = t.get("truth_provenance") if isinstance(t.get("truth_provenance"), dict) else {}
        if _ffees(t) is not None:
            row["fees_known_count"] += 1
        if any(t.get(k) is not None for k in ("entry_slippage_bps", "exit_slippage_bps", "realized_move_bps")):
            row["slippage_known_count"] += 1
        if prov.get("fees_unknown"):
            row["representation_quality_score"] = min(row["representation_quality_score"], 70.0)
        if prov.get("slippage_unknown"):
            row["representation_quality_score"] = min(row["representation_quality_score"], 75.0)
        if prov.get("databank_only"):
            row["source"] = "databank_only"
        if str(t.get("exit_reason") or "") == "stop_loss" or bool(t.get("hard_stop_exit")):
            row["hard_stop_exit_count"] += 1
        af = t.get("anomaly_flags")
        if isinstance(af, list) and len(af) > 0:
            row["trades_with_anomaly_flags"] += 1
        if str(t.get("unit") or "") == "play_money":
            row["play_money_trade_count"] += 1
        else:
            row["usd_labeled_trade_count"] += 1

    for row in by_av.values():
        n = max(1, int(row["trade_count"]))
        known = row["fees_known_count"] + row["slippage_known_count"]
        row["representation_quality_score"] = min(
            float(row["representation_quality_score"]),
            40.0 + 60.0 * (known / (2 * n)),
        )

    return {"by_avenue": by_av}
