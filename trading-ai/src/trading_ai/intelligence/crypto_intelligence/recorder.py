"""Record candidate / rejection / linkage events (append-only JSONL).

This module never places orders and never weakens safety gates.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from trading_ai.intelligence.crypto_intelligence.features import extract_structure_features
from trading_ai.intelligence.crypto_intelligence.paths import (
    candidate_events_jsonl_path,
    rejection_events_jsonl_path,
    trade_outcome_link_jsonl_path,
)


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append(path: Path, row: Dict[str, Any], *, dedup_key: str, dedup_val: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Lightweight best-effort dedupe: skip if the last ~200 lines already contain this dedup token.
    # This avoids O(N) full-file scans in hot paths and avoids any dependency on databank trade_id semantics.
    try:
        if path.is_file():
            tail = path.read_text(encoding="utf-8").splitlines()[-200:]
            tok = f"\"{dedup_key}\": \"{dedup_val}\""
            if any(tok in ln for ln in tail):
                return False
    except Exception:
        pass
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")
    return True


def record_gate_b_candidate_event(
    *,
    runtime_root: Optional[Path],
    row: Mapping[str, Any],
    stage: str,
    passed: bool,
    rejection_kind: Optional[str] = None,
    failure_codes: Optional[Sequence[str]] = None,
    detail: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Records a candidate *seen/evaluated* event for Gate B scan rows.
    """
    pid = str(row.get("product_id") or "").strip().upper()
    ts = float(row.get("quote_ts") or time.time())
    venue = str(row.get("venue") or "coinbase").strip().lower()
    gate_id = "gate_b"
    feats = extract_structure_features(row, product_id=pid, venue=venue, gate_id=gate_id, timestamp_unix=ts)
    cand_id = f"gbcand_{pid}_{int(ts)}"
    base = {
        "truth_version": "crypto_candidate_event_v1",
        "generated_at_utc": _iso(),
        "event_id": cand_id + ":" + str(stage),
        "candidate_id": cand_id,
        "stage": str(stage or "unknown"),
        "passed": bool(passed),
        "product_id": pid,
        "venue": venue,
        "gate_id": gate_id,
        "timestamp_unix": ts,
        "setup_family": feats.setup_family,
        "setup_appearance": feats.setup_appearance,
        "features": feats.to_dict(),
        "failure_codes": list(failure_codes or []),
        "rejection_kind": str(rejection_kind or "") or None,
        "detail": dict(detail or {}),
        "honesty": "Candidate event derived from Gate B scan row; candle features require closes list.",
    }
    p = candidate_events_jsonl_path(runtime_root)
    _append(p, base, dedup_key="event_id", dedup_val=cand_id + ":" + str(stage))
    if not passed:
        rej = {
            **base,
            "truth_version": "crypto_rejection_event_v1",
            "rejection_reason": base.get("rejection_kind") or "unknown",
        }
        rp = rejection_events_jsonl_path(runtime_root)
        _append(rp, rej, dedup_key="event_id", dedup_val=cand_id + ":" + str(stage))
    return {"ok": True, "candidate_id": cand_id, "setup_family": feats.setup_family}


def record_micro_candidate_decision(
    *,
    runtime_root: Optional[Path],
    product_id: str,
    gate_id: str,
    venue: str,
    quote_usd: float,
    should_trade: bool,
    rejection_reasons: Sequence[str],
    candidate: Mapping[str, Any],
    extra: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Records the micro-live decision outcome even when candle structure is unavailable.
    """
    pid = str(product_id or "").strip().upper()
    now = time.time()
    row = {"product_id": pid}  # No candle sequence available here by design.
    feats = extract_structure_features(row, product_id=pid, venue=venue, gate_id=gate_id, timestamp_unix=now)
    cand_id = str(candidate.get("candidate_id") or f"microcand_{pid}_{int(now)}")
    body = {
        "truth_version": "crypto_micro_candidate_decision_v1",
        "generated_at_utc": _iso(),
        "event_id": cand_id + ":micro_decision",
        "candidate_id": cand_id,
        "product_id": pid,
        "venue": str(venue or "").strip().lower() or "unknown",
        "gate_id": str(gate_id or "").strip().lower() or "unknown",
        "quote_usd": float(quote_usd),
        "should_trade": bool(should_trade),
        "rejection_reasons": list(rejection_reasons or []),
        "setup_family": feats.setup_family,
        "features": feats.to_dict(),
        "candidate_contract": dict(candidate),
        "extra": dict(extra or {}),
        "honesty": "Micro-live candidate decisions do not include candle sequences; structure features may be missing_or_thin.",
    }
    p = candidate_events_jsonl_path(runtime_root)
    _append(p, body, dedup_key="event_id", dedup_val=cand_id + ":micro_decision")
    if not should_trade:
        rp = rejection_events_jsonl_path(runtime_root)
        _append(
            rp,
            {**body, "truth_version": "crypto_rejection_event_v1"},
            dedup_key="event_id",
            dedup_val=cand_id + ":micro_reject",
        )
    return {"ok": True, "candidate_id": cand_id, "setup_family": feats.setup_family}


def link_trade_to_candidate(
    *,
    runtime_root: Optional[Path],
    trade_id: str,
    candidate_id: str,
    setup_family: str,
    gate_id: str,
    product_id: str,
    venue: str,
) -> Dict[str, Any]:
    tid = str(trade_id or "").strip()
    cid = str(candidate_id or "").strip()
    if not tid or not cid:
        return {"ok": False, "error": "missing_trade_or_candidate_id"}
    row = {
        "truth_version": "crypto_trade_candidate_link_v1",
        "generated_at_utc": _iso(),
        "trade_id": tid,
        "candidate_id": cid,
        "setup_family": str(setup_family or ""),
        "gate_id": str(gate_id or ""),
        "product_id": str(product_id or ""),
        "venue": str(venue or ""),
    }
    p = trade_outcome_link_jsonl_path(runtime_root)
    _append(p, row, dedup_key="trade_id", dedup_val=tid)
    return {"ok": True, "trade_id": tid, "candidate_id": cid}

