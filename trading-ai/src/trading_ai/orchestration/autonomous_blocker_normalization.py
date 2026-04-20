"""
Canonical autonomous blocker normalization — dedupe, atomize, separate active vs historical noise.

Fail-closed: does not remove real blockers; only collapses semantic duplicates and labels stale traces.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, Union

# Semantically equivalent tokens collapse to one canonical id
_CANONICAL_EQUIV: Tuple[Tuple[str, str], ...] = (
    ("runtime_runner_daemon_verification.lock_exclusivity_verified_not_true", "lock_exclusivity_not_runtime_verified"),
    ("runtime_runner_daemon_verification.failure_stop_verified_not_true", "failure_stop_not_runtime_verified"),
    ("lock_exclusivity_not_runtime_verified", "lock_exclusivity_verified_not_true"),
    ("failure_stop_not_runtime_verified", "failure_stop_verified_not_true"),
)

_EQUIV_TO_CANONICAL: Dict[str, str] = {}
for a, b in _CANONICAL_EQUIV:
    _EQUIV_TO_CANONICAL[a] = b
    _EQUIV_TO_CANONICAL[b] = b

# When runtime consistency is green, these substring patterns are treated as historical only
# if they appear only via last_failure / old chains (caller passes them in historical_inputs).
_STALE_WHEN_CONSISTENCY_GREEN_SUBSTRINGS: Tuple[str, ...] = (
    "runtime_root_or_env_fingerprint_mismatch_vs_daemon_live_switch_authority",
)

_UMBRELLA_DAEMON_VERIFICATION = "daemon_verification_incomplete"
_ATOMICS_REPLACING_UMBRELLA: frozenset = frozenset(
    {
        "lock_exclusivity_not_runtime_verified",
        "failure_stop_not_runtime_verified",
        "lock_exclusivity_verified_not_true",
        "failure_stop_verified_not_true",
        "continuous_daemon_verification_flags_incomplete",
        "runtime_runner_daemon_verification.lock_exclusivity_verified_not_true",
        "runtime_runner_daemon_verification.failure_stop_verified_not_true",
    }
)


def _split_chain(raw: str) -> List[str]:
    s = str(raw or "").strip()
    if not s:
        return []
    parts = re.split(r"[;\n]+", s)
    out: List[str] = []
    for p in parts:
        for sub in p.split(","):
            t = sub.strip()
            if t:
                out.append(t)
    return out


def _flatten_inputs(inputs: Sequence[Union[str, List[Any], None]]) -> List[str]:
    out: List[str] = []
    for item in inputs:
        if item is None:
            continue
        if isinstance(item, str):
            out.extend(_split_chain(item))
        elif isinstance(item, (list, tuple)):
            for x in item:
                out.extend(_flatten_inputs([x]))
        else:
            out.extend(_split_chain(str(item)))
    return out


def _canonical_token(tok: str) -> str:
    t = tok.strip()
    low = t.lower()
    # strip common noisy prefixes for dedup only (preserve raw separately)
    if low.startswith("daemon_authority:"):
        inner = t.split(":", 1)[1].strip()
        return _canonical_token(inner) if inner else t
    if low.startswith("autonomous:"):
        inner = t.split(":", 1)[1].strip()
        return _canonical_token(inner) if inner else t
    if low.startswith("switch_live:"):
        return t  # keep switch detail
    return _EQUIV_TO_CANONICAL.get(t, t)


def parse_consecutive_cycle_blocker(token: str) -> Optional[Dict[str, Any]]:
    """
    Parse ``insufficient_consecutive_autonomous_live_ok_cycles_need_N_have_M`` into structured fields.
    """
    t = str(token or "").strip()
    m = re.match(
        r"^insufficient_consecutive_autonomous_live_ok_cycles_need_(\d+)_have_(\d+)$",
        t,
    )
    if not m:
        return None
    need = int(m.group(1))
    have = int(m.group(2))
    return {
        "blocker_kind": "insufficient_consecutive_autonomous_live_ok_cycles",
        "required": need,
        "current": have,
        "remaining": max(0, need - have),
        "canonical_token": t,
    }


def normalize_autonomous_blockers(
    *,
    raw_blocker_inputs: Sequence[Union[str, List[Any], None]],
    runtime_consistency_green: bool,
    historical_raw_inputs: Optional[Sequence[Union[str, List[Any], None]]] = None,
) -> Dict[str, Any]:
    """
    Returns deterministic deduped autonomous blocker view.

    - **active_blockers**: current policy/runtime blockers (may omit umbrella if atomics present).
    - **historical_or_stale_blockers**: traces not treated as active (e.g. stale mismatch when consistency green).
    """
    raw_flat = _flatten_inputs(list(raw_blocker_inputs))
    hist_flat = _flatten_inputs(list(historical_raw_inputs or []))

    raw_chain = "; ".join(raw_flat)

    canonical_seen: Set[str] = set()
    atomic_ordered: List[str] = []
    for tok in raw_flat:
        c = _canonical_token(tok)
        if c not in canonical_seen:
            canonical_seen.add(c)
            atomic_ordered.append(c)

    historical: List[str] = []
    active_candidates = list(atomic_ordered)

    # Move stale mismatch tokens to historical when consistency is green
    if runtime_consistency_green:
        still_active: List[str] = []
        for t in active_candidates:
            if any(sub in t for sub in _STALE_WHEN_CONSISTENCY_GREEN_SUBSTRINGS):
                historical.append(t)
            else:
                still_active.append(t)
        active_candidates = still_active

    for h in hist_flat:
        hh = _canonical_token(h)
        if hh not in historical:
            historical.append(hh)

    # Collapse umbrella daemon_verification_incomplete if atomics present
    atomics_present = _ATOMICS_REPLACING_UMBRELLA.intersection(set(active_candidates))
    filtered_active: List[str] = []
    for t in active_candidates:
        if t == _UMBRELLA_DAEMON_VERIFICATION and atomics_present:
            continue
        filtered_active.append(t)

    # Legacy: authoritative_global_halt_blocks_autonomous was overloaded with stale-only; prefer atomic stale token.
    if (
        "stale_global_halt_classification_autonomous_forbidden" in filtered_active
        and "authoritative_global_halt_blocks_autonomous" in filtered_active
    ):
        filtered_active = [x for x in filtered_active if x != "authoritative_global_halt_blocks_autonomous"]

    # Grouped: stable buckets
    grouped: Dict[str, List[str]] = {
        "halt_and_governance": [],
        "consistency_and_authority": [],
        "daemon_verification_and_runtime_proof": [],
        "cycles_and_loop": [],
        "switch_and_abort": [],
        "other": [],
    }
    for t in filtered_active:
        tl = t.lower()
        if "halt" in tl or "governance" in tl or "stale_global" in tl:
            grouped["halt_and_governance"].append(t)
        elif "fingerprint" in tl or "mismatch" in tl or "daemon_authority" in tl or "consistency" in tl:
            grouped["consistency_and_authority"].append(t)
        elif "verification" in tl or "failure_stop" in tl or "lock" in tl or "daemon_context" in tl:
            grouped["daemon_verification_and_runtime_proof"].append(t)
        elif "consecutive" in tl or "loop" in tl or "rebuy" in tl or "final_execution" in tl:
            grouped["cycles_and_loop"].append(t)
        elif "switch_live" in tl or "daemon_abort" in tl:
            grouped["switch_and_abort"].append(t)
        else:
            grouped["other"].append(t)

    cycle_struct: Optional[Dict[str, Any]] = None
    for t in filtered_active:
        cycle_struct = parse_consecutive_cycle_blocker(t)
        if cycle_struct:
            break

    counts: Dict[str, int] = {
        "atomic": len(filtered_active),
        "historical": len(historical),
        "raw_tokens_before_dedup": len(raw_flat),
    }

    deduped_chain = "; ".join(filtered_active)

    return {
        "atomic_blockers": list(filtered_active),
        "grouped_blockers": grouped,
        "deduped_blocker_chain_string": deduped_chain,
        "historical_or_stale_blockers": historical,
        "active_blockers": list(filtered_active),
        "blocker_counts": counts,
        "consecutive_cycle_progress": cycle_struct,
        "raw_autonomous_reason_chain": raw_chain,
        "autonomous_blocker_debug": {
            "runtime_consistency_green_applied": runtime_consistency_green,
            "umbrella_daemon_verification_suppressed": bool(atomics_present),
            "canonical_merge_map_note": "Semantic equivalents map to one atomic token where listed in _CANONICAL_EQUIV.",
        },
    }


def extract_historical_from_last_failure_json(last_fail: Optional[Mapping[str, Any]]) -> List[str]:
    """Pull human-readable blocker hints from runtime_runner_last_failure without treating as active."""
    if not last_fail or not isinstance(last_fail, dict):
        return []
    out: List[str] = []
    fr = last_fail.get("failure_reason")
    if isinstance(fr, str) and fr.strip():
        out.append(fr.strip())
    b = last_fail.get("blockers")
    if isinstance(b, list):
        out.extend(str(x).strip() for x in b if str(x).strip())
    inner = last_fail.get("avenue_a_daemon")
    if isinstance(inner, dict):
        lv = inner.get("live_validation") or {}
        if isinstance(lv, dict):
            for k in ("failure_reason", "error"):
                v = lv.get(k)
                if isinstance(v, str) and v.strip():
                    out.append(v.strip())
    return _flatten_inputs([out])
