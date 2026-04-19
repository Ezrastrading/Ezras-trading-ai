"""
Multi-avenue parity contract — ``avenue_parity_report.json`` (no live trading).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.global_layer.avenue_truth_contract import expected_truth_avenues, label_play_money
from trading_ai.global_layer.trade_truth import avenue_fairness_rollups, load_federated_trades
from trading_ai.nte.memory.store import MemoryStore

# Avenues with present==True and quality >= this are eligible for "parity_equal" labeling.
DATA_QUALITY_EQUALITY_THRESHOLD = 85.0


def build_avenue_parity_report(*, nte_store: Optional[MemoryStore] = None) -> Dict[str, Any]:
    """Federation-backed parity snapshot for expected avenues."""
    from trading_ai.global_layer.avenue_truth_contract import build_representation_status

    ms = nte_store or MemoryStore()
    ms.ensure_defaults()
    trades, meta = load_federated_trades(nte_store=ms)
    exp = expected_truth_avenues()
    roll = avenue_fairness_rollups(trades)["by_avenue"]
    rep = build_representation_status(by_avenue_counts=roll, expected=exp)
    # Avenues with trades but not in ``expected`` still need representation rows for parity view.
    for av in list(roll.keys()):
        if av not in rep and isinstance(roll.get(av), dict):
            tc = int(roll[av].get("trade_count") or 0)
            rep[av] = {
                "representation": "fully_represented" if tc else "missing",
                "present": tc > 0,
                "partial": False,
                "missing": tc == 0,
                "trade_count": tc,
                "note": "unexpected_avenue_with_trades" if tc else None,
            }

    avenues: List[Dict[str, Any]] = []
    fairness_warnings: List[str] = list(meta.get("fairness_warnings") or meta.get("warnings") or [])

    all_keys = sorted(set(exp) | set(roll.keys()))
    for av in all_keys:
        row = roll.get(av) if isinstance(roll.get(av), dict) else {}
        rinfo = rep.get(av) if isinstance(rep.get(av), dict) else {}
        present = bool(rinfo.get("present"))
        missing = bool(rinfo.get("missing"))
        partial = bool(rinfo.get("partial"))
        q = float(row.get("representation_quality_score") or 0.0)
        tc = int(row.get("trade_count") or 0)
        closed = int(row.get("closed_trade_count") or tc)
        pnl = float(row.get("net_pnl_usd") or 0.0)
        hs = int(row.get("hard_stop_exit_count") or 0)
        anom = int(row.get("trades_with_anomaly_flags") or 0)
        pm = int(row.get("play_money_trade_count") or 0)
        usd = int(row.get("usd_labeled_trade_count") or 0)
        unit_type = "play_money" if pm > 0 and usd == 0 else "mixed" if pm and usd else "usd"
        ing = min(
            1.0,
            (float(row.get("fees_known_count") or 0) + float(row.get("slippage_known_count") or 0))
            / max(1.0, 2.0 * max(1, tc)),
        )

        may_equal = (
            present
            and q >= DATA_QUALITY_EQUALITY_THRESHOLD
            and not partial
            and not label_play_money(av)
            and unit_type != "play_money"
        )
        if missing or partial or not may_equal:
            if av in exp and missing:
                fairness_warnings.append(f"parity_gap:{av}:missing_expected_avenue")
            elif partial:
                fairness_warnings.append(f"parity_gap:{av}:partial_ingest_or_unknown_fields")

        avenues.append(
            {
                "avenue": av,
                "expected": av in exp,
                "present": present,
                "partial": partial,
                "missing": missing,
                "trade_count": tc,
                "closed_count": closed,
                "pnl_usd": pnl,
                "anomaly_trade_count": anom,
                "hard_stop_count": hs,
                "unit_type": unit_type,
                "data_quality_score": q,
                "ingestion_completeness_0_1": round(ing, 4),
                "may_treat_as_equal_to_peer": may_equal,
            }
        )

    return {
        "schema": "avenue_parity_report_v1",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "data_quality_equality_threshold": DATA_QUALITY_EQUALITY_THRESHOLD,
        "play_money_separate_from_usd": True,
        "avenues": avenues,
        "fairness_warnings": sorted(set(fairness_warnings)),
        "databank_root": meta.get("databank_root"),
        "merged_trade_count": meta.get("merged_trade_count"),
    }


def write_avenue_parity_report(runtime_root: Path, *, nte_store: Optional[MemoryStore] = None) -> Path:
    runtime_root = runtime_root.resolve()
    payload = build_avenue_parity_report(nte_store=nte_store)
    d = runtime_root / "parity_proof"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "avenue_parity_report.json"
    p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return p
