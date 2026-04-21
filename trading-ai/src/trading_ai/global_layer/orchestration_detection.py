"""Heuristic anomaly detection and deterministic signal conflict resolution (no hidden trades)."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.global_layer.bot_registry import load_registry
from trading_ai.global_layer.execution_intent_idempotency import intent_ledger_stats
from trading_ai.global_layer.orchestration_conflicts import load_recent_conflicts
from trading_ai.global_layer.orchestration_paths import orchestration_detection_snapshot_path
from trading_ai.global_layer.orchestration_schema import MAX_BOTS_GLOBAL, MAX_BOTS_PER_AVENUE, MAX_BOTS_PER_GATE
from trading_ai.global_layer.bot_types import BotLifecycleState

_ACTIVE_LIKE = frozenset(
    {
        BotLifecycleState.INITIALIZED.value,
        BotLifecycleState.SHADOW.value,
        BotLifecycleState.ELIGIBLE.value,
        BotLifecycleState.PROBATION.value,
        BotLifecycleState.ACTIVE.value,
        BotLifecycleState.PROMOTED.value,
        BotLifecycleState.DEGRADED.value,
    }
)


def _is_active_like(b: Dict[str, Any]) -> bool:
    return str(b.get("lifecycle_state") or "") in _ACTIVE_LIKE


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_conflicting_signals(signals: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Deterministic: if both BUY and SELL intent exist for the same ``symbol``, **no_trade**.

    Each signal: ``{"bot_id", "side": "buy"|"sell", "symbol": "BTC-USD"}``.
    """
    by_sym: Dict[str, Counter] = {}
    for s in signals:
        sym = str(s.get("symbol") or "").strip().upper()
        side = str(s.get("side") or "").strip().lower()
        if not sym or side not in ("buy", "sell"):
            continue
        by_sym.setdefault(sym, Counter())[side] += 1
    conflicts = [sym for sym, ctr in by_sym.items() if ctr.get("buy", 0) > 0 and ctr.get("sell", 0) > 0]
    if conflicts:
        return {
            "truth_version": "signal_conflict_resolution_v1",
            "action": "no_trade",
            "reason": "opposing_signals_same_symbol",
            "symbols": sorted(conflicts),
            "honesty": "No weighted tie-break; escalate to CEO review artifact.",
        }
    return {"truth_version": "signal_conflict_resolution_v1", "action": "allow_routing", "reason": "no_opposing_pair"}


def detect_bot_registry_anomalies(*, registry_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    reg = load_registry(registry_path)
    bots = list(reg.get("bots") or [])
    active = [b for b in bots if _is_active_like(b)]
    findings: List[Dict[str, Any]] = []
    if len(active) >= MAX_BOTS_GLOBAL * 0.9:
        findings.append({"kind": "near_global_bot_cap", "active": len(active), "cap": MAX_BOTS_GLOBAL})
    by_av: Dict[str, int] = {}
    by_gate: Dict[str, int] = {}
    for b in active:
        av = str(b.get("avenue") or "")
        g = str(b.get("gate") or "none")
        by_av[av] = by_av.get(av, 0) + 1
        by_gate[f"{av}|{g}"] = by_gate.get(f"{av}|{g}", 0) + 1
    for av, n in by_av.items():
        if n >= MAX_BOTS_PER_AVENUE:
            findings.append({"kind": "avenue_bot_cap_breach", "avenue": av, "count": n, "cap": MAX_BOTS_PER_AVENUE})
        elif n >= MAX_BOTS_PER_AVENUE - 1:
            findings.append({"kind": "near_avenue_bot_cap", "avenue": av, "count": n, "cap": MAX_BOTS_PER_AVENUE})
    for gk, n in by_gate.items():
        if n >= MAX_BOTS_PER_GATE:
            findings.append({"kind": "gate_bot_cap_breach", "gate_key": gk, "count": n, "cap": MAX_BOTS_PER_GATE})

    staleish = [b for b in bots if str(b.get("status") or "") == "stale"]
    if len(staleish) >= 3:
        findings.append({"kind": "stale_bot_cluster", "count": len(staleish)})

    dup_keys: Dict[str, int] = {}
    for b in bots:
        dg = str(b.get("duplicate_guard_key") or "")
        if dg:
            dup_keys[dg] = dup_keys.get(dg, 0) + 1
    for k, v in dup_keys.items():
        if v > 1:
            findings.append({"kind": "duplicate_guard_collision", "duplicate_guard_key": k, "count": v})
    return findings


def detect_execution_anomalies(*, registry_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    stats = intent_ledger_stats()
    if int(stats.get("line_count") or 0) > 100_000:
        out.append({"kind": "large_intent_ledger", **stats})
    recent = load_recent_conflicts(500)
    if len(recent) >= 50:
        out.append({"kind": "elevated_conflict_log_volume", "recent_lines": len(recent)})
    out.extend(detect_bot_registry_anomalies(registry_path=registry_path))
    return out


def write_detection_snapshot(*, registry_path: Optional[Path] = None) -> Dict[str, Any]:
    payload = {
        "truth_version": "orchestration_detection_snapshot_v1",
        "generated_at": _iso(),
        "execution_anomalies": detect_execution_anomalies(registry_path=registry_path),
        "bot_anomalies": detect_bot_registry_anomalies(registry_path=registry_path),
    }
    p = orchestration_detection_snapshot_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload
