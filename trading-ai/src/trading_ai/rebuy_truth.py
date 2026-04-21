"""
Rebuy truth system.

Rebuy ONLY IF:
- prior trade FULLY CLOSED (truth chain PASS)
- net profit confirmed
- new opportunity validated independently (caller supplies validation evidence refs)

Adds:
- cooldown timer
- concurrency guard (no overlapping cycles)
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.storage.storage_adapter import LocalStorageAdapter
from trading_ai.truth_engine import truth_chain_for_post_trade, validate_truth_chain


@dataclass(frozen=True)
class RebuyPolicy:
    cooldown_seconds: float = 180.0
    lock_ttl_seconds: float = 900.0


def _now() -> float:
    return float(time.time())


def _read_json(ad: LocalStorageAdapter, rel: str) -> Dict[str, Any]:
    j = ad.read_json(rel)
    return j if isinstance(j, dict) else {}


def _write(ad: LocalStorageAdapter, rel: str, payload: Dict[str, Any]) -> None:
    ad.write_json(rel, payload)


def _acquire_cycle_lock(ad: LocalStorageAdapter, policy: RebuyPolicy) -> bool:
    rel = "data/control/cycle_lock.json"
    cur = _read_json(ad, rel)
    ts = float(cur.get("timestamp") or 0.0)
    if cur.get("locked") and (_now() - ts) < float(policy.lock_ttl_seconds):
        return False
    _write(ad, rel, {"locked": True, "timestamp": _now()})
    return True


def _release_cycle_lock(ad: LocalStorageAdapter) -> None:
    _write(ad, "data/control/cycle_lock.json", {"locked": False, "timestamp": _now()})


def evaluate_rebuy_truth(
    *,
    runtime_root: Optional[Path] = None,
    opportunity_evidence: Optional[List[str]] = None,
    policy: Optional[RebuyPolicy] = None,
) -> Dict[str, Any]:
    """
    Writes `data/control/rebuy_decision_truth.json` and returns it.
    """
    pol = policy or RebuyPolicy()
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    root = ad.root()

    locked = _acquire_cycle_lock(ad, pol)
    if not locked:
        out = {
            "rebuy_allowed": False,
            "reason": "concurrency_guard:cycle_lock_active",
            "cooldown_seconds": float(pol.cooldown_seconds),
            "timestamp": _now(),
        }
        _write(ad, "data/control/rebuy_decision_truth.json", out)
        return out

    try:
        last = _read_json(ad, "data/pnl/pnl_record.json")
        net = last.get("net_pnl")
        try:
            net_v = float(net) if net is not None else None
        except Exception:
            net_v = None

        truth = validate_truth_chain(truth_chain_for_post_trade(runtime_root=root))
        opp = [str(x) for x in (opportunity_evidence or []) if str(x).strip()]
        if not opp:
            out = {
                "rebuy_allowed": False,
                "reason": "opportunity_not_validated_independently",
                "required": ["independent_opportunity_evidence_refs"],
                "truth_chain": truth,
                "timestamp": _now(),
            }
            _write(ad, "data/control/rebuy_decision_truth.json", out)
            return out

        # Cooldown uses last_decision timestamp when present.
        last_dec = _read_json(ad, "data/control/last_decision.json")
        last_ts = float(last_dec.get("timestamp") or 0.0)
        in_cooldown = last_ts > 0 and (_now() - last_ts) < float(pol.cooldown_seconds)

        if not bool(truth.get("ok")):
            out = {
                "rebuy_allowed": False,
                "reason": "prior_trade_not_fully_validated",
                "truth_chain": truth,
                "timestamp": _now(),
                "opportunity_evidence": opp,
            }
            _write(ad, "data/control/rebuy_decision_truth.json", out)
            return out

        if net_v is None:
            out = {
                "rebuy_allowed": False,
                "reason": "net_profit_unavailable",
                "truth_chain": truth,
                "timestamp": _now(),
                "opportunity_evidence": opp,
            }
            _write(ad, "data/control/rebuy_decision_truth.json", out)
            return out

        if net_v <= 0:
            out = {
                "rebuy_allowed": False,
                "reason": "net_profit_not_confirmed",
                "net_pnl": float(net_v),
                "truth_chain": truth,
                "timestamp": _now(),
                "opportunity_evidence": opp,
            }
            _write(ad, "data/control/rebuy_decision_truth.json", out)
            return out

        if in_cooldown:
            out = {
                "rebuy_allowed": False,
                "reason": "cooldown_active",
                "cooldown_seconds": float(pol.cooldown_seconds),
                "timestamp": _now(),
                "opportunity_evidence": opp,
            }
            _write(ad, "data/control/rebuy_decision_truth.json", out)
            return out

        out = {
            "rebuy_allowed": True,
            "reason": "ok",
            "net_pnl": float(net_v),
            "timestamp": _now(),
            "truth_chain": truth,
            "opportunity_evidence": opp,
        }
        _write(ad, "data/control/rebuy_decision_truth.json", out)
        return out
    finally:
        _release_cycle_lock(ad)

