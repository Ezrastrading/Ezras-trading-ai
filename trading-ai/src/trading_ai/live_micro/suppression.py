from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def _read_json(p: Path) -> Dict[str, Any]:
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _write_json(p: Path, payload: Dict[str, Any]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    tmp.replace(p)


def suppression_state_path(runtime_root: Path) -> Path:
    root = Path(runtime_root).resolve()
    return root / "data" / "control" / "live_micro_suppression_state.json"


@dataclass(frozen=True)
class SuppressionDecision:
    suppressed: bool
    reason: str
    until_ts: Optional[float]
    meta: Dict[str, Any]


def _now() -> float:
    return time.time()


def load_suppression_state(runtime_root: Path) -> Dict[str, Any]:
    st = _read_json(suppression_state_path(runtime_root))
    if not isinstance(st.get("product_cooldowns"), dict):
        st["product_cooldowns"] = {}
    if not isinstance(st.get("quote_wallet_cooldowns"), dict):
        st["quote_wallet_cooldowns"] = {}
    if not isinstance(st.get("candidate_fingerprints"), dict):
        st["candidate_fingerprints"] = {}
    return st


def save_suppression_state(runtime_root: Path, st: Dict[str, Any]) -> None:
    st = dict(st or {})
    st["truth_version"] = "live_micro_suppression_state_v1"
    st["updated_at_unix"] = _now()
    _write_json(suppression_state_path(runtime_root), st)


def _prune_expired(m: Dict[str, Any]) -> Dict[str, Any]:
    now = _now()
    out: Dict[str, Any] = {}
    for k, v in (m or {}).items():
        if not isinstance(v, dict):
            continue
        try:
            until = float(v.get("until_ts") or 0.0)
        except Exception:
            until = 0.0
        if until and until > now:
            out[str(k)] = dict(v)
    return out


def set_product_cooldown(
    *,
    runtime_root: Path,
    product_id: str,
    seconds: float,
    reason: str,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    st = load_suppression_state(runtime_root)
    st["product_cooldowns"] = _prune_expired(dict(st.get("product_cooldowns") or {}))
    until = _now() + max(1.0, float(seconds))
    st["product_cooldowns"][str(product_id).upper()] = {
        "until_ts": until,
        "reason": str(reason),
        "meta": dict(meta or {}),
    }
    save_suppression_state(runtime_root, st)


def set_quote_wallet_cooldown(
    *,
    runtime_root: Path,
    quote_ccy: str,
    seconds: float,
    reason: str,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    st = load_suppression_state(runtime_root)
    st["quote_wallet_cooldowns"] = _prune_expired(dict(st.get("quote_wallet_cooldowns") or {}))
    until = _now() + max(1.0, float(seconds))
    st["quote_wallet_cooldowns"][str(quote_ccy).upper()] = {
        "until_ts": until,
        "reason": str(reason),
        "meta": dict(meta or {}),
    }
    save_suppression_state(runtime_root, st)


def check_suppression(
    *,
    runtime_root: Path,
    product_id: str,
    quote_ccy: Optional[str] = None,
) -> SuppressionDecision:
    st = load_suppression_state(runtime_root)
    now = _now()
    prod = str(product_id).upper()
    st["product_cooldowns"] = _prune_expired(dict(st.get("product_cooldowns") or {}))
    st["quote_wallet_cooldowns"] = _prune_expired(dict(st.get("quote_wallet_cooldowns") or {}))
    save_suppression_state(runtime_root, st)

    row = st["product_cooldowns"].get(prod)
    if isinstance(row, dict):
        until = float(row.get("until_ts") or 0.0)
        if until > now:
            return SuppressionDecision(True, "product_cooldown_active", until, {"product_id": prod, **row})

    if quote_ccy:
        q = str(quote_ccy).upper()
        row2 = st["quote_wallet_cooldowns"].get(q)
        if isinstance(row2, dict):
            until = float(row2.get("until_ts") or 0.0)
            if until > now:
                return SuppressionDecision(True, "dust_wallet_cooldown_active", until, {"quote_ccy": q, **row2})

    return SuppressionDecision(False, "ok", None, {})


def candidate_fingerprint(it: Dict[str, Any]) -> str:
    """
    Stable dedupe key for noisy gate-b candidate items.
    Intentionally ignores id/ts so repeated emissions can be dropped.
    """
    pid = str(it.get("product_id") or "").strip().upper()
    gate = str(it.get("gate_id") or "").strip().lower()
    src = str(it.get("source") or "").strip().lower()
    return f"{gate}:{pid}:{src}"


def check_candidate_duplicate(
    *,
    runtime_root: Path,
    fingerprint: str,
    cooldown_sec: float,
) -> Tuple[bool, Optional[float]]:
    st = load_suppression_state(runtime_root)
    now = _now()
    m = dict(st.get("candidate_fingerprints") or {})
    # prune
    out: Dict[str, Any] = {}
    for k, v in m.items():
        try:
            ts = float(v.get("last_ts") or 0.0) if isinstance(v, dict) else float(v or 0.0)
        except Exception:
            ts = 0.0
        if ts > 0 and (now - ts) < 3600.0:
            out[str(k)] = v
    m = out
    row = m.get(fingerprint)
    last_ts = None
    if isinstance(row, dict):
        try:
            last_ts = float(row.get("last_ts") or 0.0)
        except Exception:
            last_ts = None
    if last_ts and (now - last_ts) < float(cooldown_sec):
        return True, last_ts + float(cooldown_sec)
    m[fingerprint] = {"last_ts": now}
    st["candidate_fingerprints"] = m
    save_suppression_state(runtime_root, st)
    return False, None

