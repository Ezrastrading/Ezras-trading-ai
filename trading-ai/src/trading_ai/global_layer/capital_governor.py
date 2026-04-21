"""
Capital authority (C0–C5) — separate from promotion tier. Enforces envelopes, ramps, and aggregates.

Live venue paths may call :func:`check_live_quote_allowed` when enforcement env is enabled.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.global_layer.orchestration_paths import (
    bot_capital_authority_registry_path,
    capital_freeze_events_path,
    capital_governor_policy_path,
    capital_governor_readiness_truth_path,
    capital_scale_down_queue_path,
    capital_scale_up_queue_path,
)
from trading_ai.global_layer.orchestration_schema import CapitalAuthorityTier, PromotionTier, capital_tier_index, promotion_tier_index


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bundled_capital_policy_path() -> Path:
    return Path(__file__).resolve().parent / "_governance_data" / "orchestration" / "capital_governor_policy.json"


def load_capital_governor_policy(*, path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or capital_governor_policy_path()
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    fb = _bundled_capital_policy_path()
    if fb.is_file():
        return json.loads(fb.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"capital_governor_policy_missing:{p}")


def max_eligible_capital_tier_for_promotion(promotion_tier: str, policy: Optional[Dict[str, Any]] = None) -> str:
    pol = policy or load_capital_governor_policy()
    m = pol.get("max_capital_tier_by_promotion") or {}
    pt = str(promotion_tier or PromotionTier.T0.value).strip().upper()
    return str(m.get(pt) or CapitalAuthorityTier.C0.value)


def envelope_for_tier(tier: str, policy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    pol = policy or load_capital_governor_policy()
    envs = pol.get("capital_envelopes") or {}
    t = str(tier or CapitalAuthorityTier.C0.value).strip().upper()
    e = envs.get(t)
    if isinstance(e, dict):
        return dict(e)
    return dict((envs.get("C0") or {}))


def effective_envelope(
    bot: Dict[str, Any],
    policy: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Apply ramp-after-promotion cap (fraction of envelope) until scale-up contract clears.
    Returns (envelope_dict, notes).
    """
    pol = policy or load_capital_governor_policy()
    ct = str(bot.get("capital_authority_tier") or CapitalAuthorityTier.C0.value).strip().upper()
    base = envelope_for_tier(ct, policy=pol)
    notes: List[str] = []
    ramp = float((pol.get("ramp_after_promotion_cap_pct") or 1.0))
    if bot.get("capital_ramp_complete") is True:
        return base, ["ramp_complete"]
    if ramp < 1.0 and capital_tier_index(ct) >= 1:
        out = dict(base)
        for k in ("max_quote_per_trade_usd", "max_notional_per_day_usd", "max_open_risk_usd"):
            if k in out:
                try:
                    out[k] = float(out[k]) * ramp
                except Exception:
                    pass
        notes.append(f"ramp_cap_active_pct={ramp}")
        return out, notes
    return base, notes


def load_capital_registry(*, path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or bot_capital_authority_registry_path()
    if not p.is_file():
        return {"truth_version": "bot_capital_authority_registry_v1", "bots": {}, "updated_at": None}
    return json.loads(p.read_text(encoding="utf-8"))


def save_capital_registry(data: Dict[str, Any], *, path: Optional[Path] = None) -> None:
    p = path or bot_capital_authority_registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = dict(data)
    data["truth_version"] = "bot_capital_authority_registry_v1"
    data["updated_at"] = _iso()
    p.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def sync_registry_from_bot(bot: Dict[str, Any], policy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Upsert one bot row in capital registry artifact (audit view)."""
    pol = policy or load_capital_governor_policy()
    reg = load_capital_registry()
    bots = dict(reg.get("bots") or {})
    bid = str(bot.get("bot_id") or "")
    if not bid:
        return reg
    env, notes = effective_envelope(bot, policy=pol)
    max_pt = max_eligible_capital_tier_for_promotion(str(bot.get("promotion_tier") or "T0"), policy=pol)
    bots[bid] = {
        "bot_id": bid,
        "avenue": str(bot.get("avenue") or ""),
        "gate": str(bot.get("gate") or ""),
        "route": str(bot.get("route") or "default"),
        "task_family": str(bot.get("task_family") or ""),
        "promotion_tier": str(bot.get("promotion_tier") or PromotionTier.T0.value),
        "capital_authority_tier": str(bot.get("capital_authority_tier") or CapitalAuthorityTier.C0.value),
        "max_eligible_capital_tier_for_promotion": max_pt,
        "emergency_cap_lock": bool(bot.get("emergency_cap_lock")),
        "effective_envelope": env,
        "envelope_notes": notes,
        "updated_at": _iso(),
    }
    reg["bots"] = bots
    save_capital_registry(reg)
    return reg


def _aggregate_usage_placeholder() -> Dict[str, Any]:
    """Placeholder until wired to ledger — deterministic zeros for tests."""
    return {"per_gate_usd": {}, "per_avenue_usd": {}, "global_usd": 0.0}


def check_live_quote_allowed(
    bot: Optional[Dict[str, Any]],
    quote_usd: float,
    *,
    avenue: str,
    gate: str,
    route: str = "default",
    policy: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Return (allowed, reason, diagnostics). If ``bot`` is None, deny when enforcement expects a bot.
    """
    pol = policy or load_capital_governor_policy()
    diag: Dict[str, Any] = {"quote_usd": quote_usd, "checks": {}}
    if bot is None:
        return False, "no_bot_context", diag

    if bool(bot.get("emergency_cap_lock")):
        diag["checks"]["emergency_cap_lock"] = {"ok": False}
        return False, "emergency_cap_lock", diag

    ct = str(bot.get("capital_authority_tier") or CapitalAuthorityTier.C0.value).strip().upper()
    if capital_tier_index(ct) <= 0:
        diag["checks"]["capital_tier"] = {"ok": False, "reason": "C0_no_capital"}
        return False, "capital_tier_C0_blocks_quote", diag

    env, _notes = effective_envelope(bot, policy=pol)
    max_q = float(env.get("max_quote_per_trade_usd") or 0.0)
    diag["effective_max_quote_per_trade_usd"] = max_q
    diag["checks"]["per_trade_cap"] = {"ok": quote_usd <= max_q + 1e-9, "max": max_q}
    if quote_usd > max_q:
        return False, f"quote_exceeds_cap:{quote_usd}>{max_q}", diag

    ag = pol.get("aggregate_caps") or {}
    usage = _aggregate_usage_placeholder()
    gate_cap = float(ag.get("per_gate_max_notional_usd") or 1e15)
    ave_cap = float(ag.get("per_avenue_max_notional_usd") or 1e15)
    glob_cap = float(ag.get("global_max_notional_usd") or 1e15)
    gk = f"{avenue}|{gate}"
    gate_used = float((usage.get("per_gate_usd") or {}).get(gk) or 0.0)
    ave_used = float((usage.get("per_avenue_usd") or {}).get(avenue) or 0.0)
    glob_used = float(usage.get("global_usd") or 0.0)
    if gate_used + quote_usd > gate_cap:
        return False, "per_gate_aggregate_cap", {**diag, "gate_used": gate_used, "gate_cap": gate_cap}
    if ave_used + quote_usd > ave_cap:
        return False, "per_avenue_aggregate_cap", {**diag, "ave_used": ave_used}
    if glob_used + quote_usd > glob_cap:
        return False, "global_aggregate_cap", {**diag, "glob_used": glob_used}

    return True, "ok", diag


def evaluate_capital_scale_up(
    bot: Dict[str, Any],
    policy: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Deterministic single-step scale-up eligibility (C_i -> C_{i+1})."""
    pol = policy or load_capital_governor_policy()
    sc = bot.get("promotion_scorecard") if isinstance(bot.get("promotion_scorecard"), dict) else {}
    contract = pol.get("scale_up_contract") or {}
    pt = str(bot.get("promotion_tier") or PromotionTier.T0.value)
    ct = str(bot.get("capital_authority_tier") or CapitalAuthorityTier.C0.value)
    max_elig = max_eligible_capital_tier_for_promotion(pt, policy=pol)
    ci, mi = capital_tier_index(ct), capital_tier_index(max_elig)
    out: Dict[str, Any] = {
        "truth_version": "capital_scale_up_eval_v1",
        "evaluated_at": _iso(),
        "bot_id": str(bot.get("bot_id") or ""),
        "current_capital_tier": ct,
        "max_eligible_capital_tier": max_elig,
        "allowed": False,
        "target_tier": ct,
        "reasons": [],
    }
    if ci >= mi:
        out["reasons"].append("already_at_or_beyond_promotion_eligible_capital_tier")
        return out
    if ci >= 5:
        out["reasons"].append("already_C5")
        return out

    need_cycles = int(contract.get("min_clean_live_cycles_per_step") or 999999)
    have_cycles = int(sc.get("clean_live_cycles") or 0)
    if have_cycles < need_cycles:
        out["reasons"].append(f"clean_live_cycles_need_{need_cycles}_have_{have_cycles}")
    dd = float(sc.get("max_drawdown_pct") or 0.0)
    _raw_dd = contract.get("max_drawdown_pct_for_scale")
    max_dd = float(_raw_dd) if _raw_dd is not None else 0.0
    if dd > max_dd:
        out["reasons"].append("drawdown_too_high_for_scale")
    ex = float(sc.get("expectancy") or 0.0)
    _raw_ex = contract.get("min_expectancy_for_scale")
    min_ex = float(_raw_ex) if _raw_ex is not None else 1.0
    if ex < min_ex:
        out["reasons"].append("expectancy_too_low_for_scale")
    if bool(bot.get("emergency_cap_lock")):
        out["reasons"].append("emergency_cap_lock")
    if bot.get("demotion_risk") is True:
        out["reasons"].append("demotion_risk")

    if not out["reasons"]:
        nxt = f"C{ci + 1}"
        out["allowed"] = capital_tier_index(nxt) <= mi
        out["target_tier"] = nxt if out["allowed"] else ct
        if not out["allowed"]:
            out["reasons"].append("target_exceeds_max_eligible_for_promotion_tier")
    return out


def apply_capital_tier(bot: Dict[str, Any], new_tier: str, reason: str, source: str) -> Dict[str, Any]:
    b = dict(bot)
    t = str(new_tier).strip().upper()
    b["capital_authority_tier"] = t
    b["last_capital_change_at"] = _iso()
    b["authority_change_reason"] = reason
    b["authority_source"] = source
    if capital_tier_index(t) <= 0:
        b["capital_mode"] = "none"
    else:
        b["capital_mode"] = "active"
    return b


def append_scale_queue(kind: str, item: Dict[str, Any]) -> None:
    p = capital_scale_up_queue_path() if kind == "up" else capital_scale_down_queue_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    cur = {"truth_version": "capital_scale_queue_v1", "items": []}
    if p.is_file():
        cur = json.loads(p.read_text(encoding="utf-8"))
    cur.setdefault("items", []).append({**item, "queued_at": _iso()})
    cur["updated_at"] = _iso()
    p.write_text(json.dumps(cur, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def append_freeze_event(event: Dict[str, Any]) -> None:
    p = capital_freeze_events_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({**event, "at": _iso()}, sort_keys=True) + "\n"
    with p.open("a", encoding="utf-8") as fh:
        fh.write(line)


def write_capital_readiness_truth(extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = {
        "truth_version": "capital_governor_readiness_v1",
        "generated_at": _iso(),
        "policy_loaded": True,
    }
    if extra:
        payload.update(extra)
    from trading_ai.global_layer.lock_layer.truth_writers import CANONICAL_WRITER_IDS, TruthDomain, finalize_capital_readiness_truth

    return finalize_capital_readiness_truth(payload, writer_id=CANONICAL_WRITER_IDS[TruthDomain.CAPITAL])


def maybe_scale_capital_after_promotion(bot: Dict[str, Any], policy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Promotion raises *eligibility ceiling* only. Capital tier still advances only via
    :func:`evaluate_capital_scale_up` / scale-up cycle — never an automatic jump here.
    """
    pol = policy or load_capital_governor_policy()
    max_elig = max_eligible_capital_tier_for_promotion(str(bot.get("promotion_tier") or "T0"), policy=pol)
    sync_registry_from_bot(bot, policy=pol)
    return {
        "bot_id": str(bot.get("bot_id")),
        "max_eligible_capital_tier_for_promotion": max_elig,
        "changed": False,
        "honesty": "capital_tier_unchanged_pending_scale_up_contract",
    }
