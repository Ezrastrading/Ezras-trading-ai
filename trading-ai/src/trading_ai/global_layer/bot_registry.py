"""Bot registry — single source of truth for registered bots (file-backed, deterministic)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.global_layer._bot_paths import default_bot_registry_path
from trading_ai.global_layer.bot_types import BotLifecycleState, BotRole
from trading_ai.global_layer.orchestration_registry_normalize import normalize_bot_record

_REGISTRY_VERSION = "bot_registry_v2"


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _migrate_registry(raw: Dict[str, Any]) -> Dict[str, Any]:
    raw = dict(raw)
    bots_in = list(raw.get("bots") or [])
    bots_out: List[Dict[str, Any]] = []
    for b in bots_in:
        try:
            bots_out.append(normalize_bot_record(dict(b)))
        except Exception:
            bots_out.append(dict(b))
    raw["bots"] = bots_out
    raw["truth_version"] = _REGISTRY_VERSION
    return raw


def load_registry(path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or default_bot_registry_path()
    if not p.is_file():
        return {"truth_version": _REGISTRY_VERSION, "updated_at": None, "bots": []}
    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("bot_registry_invalid_root")
    if str(raw.get("truth_version") or "").startswith("bot_registry_v1"):
        raw = _migrate_registry(raw)
    raw.setdefault("truth_version", _REGISTRY_VERSION)
    raw.setdefault("bots", [])
    # Ensure each bot normalized on every load
    raw["bots"] = [normalize_bot_record(dict(b)) for b in (raw.get("bots") or [])]
    return raw


def save_registry(data: Dict[str, Any], path: Optional[Path] = None) -> None:
    p = path or default_bot_registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = dict(data)
    data["truth_version"] = _REGISTRY_VERSION
    data["updated_at"] = _iso()
    data["bots"] = [normalize_bot_record(dict(b)) for b in (data.get("bots") or [])]
    p.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def _active_states() -> frozenset:
    return frozenset(
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


def register_bot(bot_config: Dict[str, Any], *, path: Optional[Path] = None, allow_duplicate_role: bool = False) -> Dict[str, Any]:
    """
    Register a bot. Duplicate ``duplicate_guard_key`` is rejected for active-like lifecycles unless
    allow_duplicate_role True with non-empty duplicate_justification (audited).
    """
    from trading_ai.global_layer.bot_policy import assert_not_live_execution_override, validate_bot_config

    assert_not_live_execution_override(bot_config)
    ok, errs = validate_bot_config(bot_config)
    if not ok:
        raise ValueError("invalid_bot_config:" + ";".join(errs))
    normalized = normalize_bot_record(dict(bot_config))
    reg = load_registry(path)
    bots: List[Dict[str, Any]] = list(reg.get("bots") or [])
    bid = str(normalized.get("bot_id") or "").strip()
    if not bid:
        raise ValueError("bot_id_required")
    if any(str(b.get("bot_id")) == bid for b in bots):
        raise ValueError(f"duplicate_bot_id:{bid}")
    role = str(normalized.get("role") or "").strip()
    if role not in {r.value for r in BotRole}:
        raise ValueError(f"invalid_role:{role}")
    life = str(normalized.get("lifecycle_state") or BotLifecycleState.PROPOSED.value).strip()
    dg = str(normalized.get("duplicate_guard_key") or "")
    if not allow_duplicate_role and life in _active_states():
        for b in bots:
            if str(b.get("lifecycle_state")) not in _active_states():
                continue
            if str(b.get("duplicate_guard_key")) == dg:
                raise ValueError(f"duplicate_scope_guard:{dg}")
    if allow_duplicate_role:
        just = str(normalized.get("duplicate_justification") or "").strip()
        if not just:
            raise ValueError("duplicate_justification_required_when_allow_duplicate_role")
    normalized.setdefault("created_at", _iso())
    normalized["updated_at"] = _iso()
    bots.append(normalized)
    reg["bots"] = bots
    save_registry(reg, path=path)
    return {"ok": True, "bot_id": bid, "registry_path": str(path or default_bot_registry_path())}


def update_bot_performance(bot_id: str, metrics: Dict[str, Any], *, path: Optional[Path] = None) -> Dict[str, Any]:
    reg = load_registry(path)
    bots = list(reg.get("bots") or [])
    found = False
    out: List[Dict[str, Any]] = []
    for b in bots:
        if str(b.get("bot_id")) != bot_id:
            out.append(b)
            continue
        found = True
        perf = dict(b.get("performance") or {})
        perf.update(metrics)
        perf["updated_at"] = _iso()
        nb = normalize_bot_record(dict(b))
        nb["performance"] = perf
        nb["updated_at"] = _iso()
        out.append(nb)
    if not found:
        raise ValueError(f"unknown_bot_id:{bot_id}")
    reg["bots"] = out
    save_registry(reg, path=path)
    return {"ok": True, "bot_id": bot_id}


def patch_bot(bot_id: str, fields: Dict[str, Any], *, path: Optional[Path] = None) -> Dict[str, Any]:
    reg = load_registry(path)
    bots = []
    found = None
    for b in reg.get("bots") or []:
        if str(b.get("bot_id")) != bot_id:
            bots.append(b)
            continue
        nb = normalize_bot_record({**dict(b), **fields})
        nb["updated_at"] = _iso()
        found = nb
        bots.append(nb)
    if not found:
        raise ValueError(f"unknown_bot_id:{bot_id}")
    reg["bots"] = bots
    save_registry(reg, path=path)
    return found


def get_bots_by_avenue(avenue: str, *, path: Optional[Path] = None) -> List[Dict[str, Any]]:
    reg = load_registry(path)
    a = str(avenue).strip()
    return [b for b in (reg.get("bots") or []) if str(b.get("avenue")) == a]


def get_bots_by_role(role: str, *, path: Optional[Path] = None) -> List[Dict[str, Any]]:
    reg = load_registry(path)
    r = str(role).strip()
    return [b for b in (reg.get("bots") or []) if str(b.get("role")) == r]


def get_bots_by_gate(gate: str, *, path: Optional[Path] = None) -> List[Dict[str, Any]]:
    reg = load_registry(path)
    g = str(gate).strip()
    return [b for b in (reg.get("bots") or []) if str(b.get("gate")) == g]


def get_bots_by_class(bot_class: str, *, path: Optional[Path] = None) -> List[Dict[str, Any]]:
    reg = load_registry(path)
    c = str(bot_class).strip()
    return [b for b in (reg.get("bots") or []) if str(b.get("bot_class")) == c]


def get_bot(bot_id: str, *, path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    reg = load_registry(path)
    for b in reg.get("bots") or []:
        if str(b.get("bot_id")) == bot_id:
            return normalize_bot_record(dict(b))
    return None

