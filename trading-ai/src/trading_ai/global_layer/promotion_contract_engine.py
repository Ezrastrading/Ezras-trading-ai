"""
Deterministic staged promotion (T0–T5). AI may recommend; this module only evaluates hard contracts.

Separate evaluators populate ``external_eval_signals`` / ``governance_flags``; the engine never trusts
the bot's self-narrative alone when ``require_external_eval_signals`` is true in policy.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.global_layer.orchestration_paths import bot_eval_signals_path, promotion_contract_policy_path
from trading_ai.global_layer.orchestration_schema import (
    PromotionTier,
    permission_and_capabilities_for_promotion_tier,
    promotion_tier_index,
)
from trading_ai.global_layer.orchestration_kill_switch import load_kill_switch


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bundled_default_policy_path() -> Path:
    return Path(__file__).resolve().parent / "_governance_data" / "orchestration" / "promotion_contract_policy.json"


def load_promotion_contract_policy(*, path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or promotion_contract_policy_path()
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    fb = _bundled_default_policy_path()
    if fb.is_file():
        return json.loads(fb.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"promotion_contract_policy_missing:{p}")


def _next_tier(current: str) -> Optional[str]:
    i = promotion_tier_index(current)
    if i >= 5:
        return None
    return f"T{i + 1}"


def _parse_ts(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        raw = str(s).replace("Z", "+00:00")
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def _age_sec(ts: Optional[str]) -> Optional[float]:
    dt = _parse_ts(ts)
    if not dt:
        return None
    now = datetime.now(timezone.utc)
    return (now - dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else (now - dt)).total_seconds()


def load_merged_eval_signals(bot: Dict[str, Any]) -> Dict[str, bool]:
    """Prefer on-disk evaluator artifact; merge with registry external_eval_signals."""
    bid = str(bot.get("bot_id") or "")
    merged = dict((bot.get("external_eval_signals") or {}) if isinstance(bot.get("external_eval_signals"), dict) else {})
    p = bot_eval_signals_path(bid)
    if p.is_file():
        try:
            disk = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(disk, dict):
                for k in ("performance_evaluator_ok", "risk_engine_ok", "truth_layer_ok", "orchestration_policy_ok"):
                    if k in disk:
                        merged[k] = bool(disk[k])
        except Exception:
            pass
    return {
        "performance_evaluator_ok": bool(merged.get("performance_evaluator_ok")),
        "risk_engine_ok": bool(merged.get("risk_engine_ok")),
        "truth_layer_ok": bool(merged.get("truth_layer_ok")),
        "orchestration_policy_ok": bool(merged.get("orchestration_policy_ok")),
    }


def evaluate_promotion_contract_detailed(
    bot: Dict[str, Any],
    *,
    policy: Optional[Dict[str, Any]] = None,
    now_iso: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Evaluate whether ``bot`` may advance exactly one promotion tier.

    Returns structured pass/fail with per-clause reasons (never a single opaque boolean).
    """
    pol = policy or load_promotion_contract_policy()
    require_sig = bool(pol.get("require_external_eval_signals"))
    current = str(bot.get("promotion_tier") or PromotionTier.T0.value).strip().upper()
    nxt = _next_tier(current)
    out: Dict[str, Any] = {
        "truth_version": "promotion_contract_evaluation_v1",
        "evaluated_at": now_iso or _iso(),
        "bot_id": str(bot.get("bot_id") or ""),
        "current_tier": current,
        "target_tier": nxt,
        "passed": False,
        "blocker": None,
        "clauses": {},
    }
    if not nxt:
        out["blocker"] = "already_at_max_tier"
        out["clauses"]["max_tier"] = {"ok": False, "reason": "already_at_T5"}
        return out

    gates = pol.get("stage_gates") or {}
    gate = gates.get(nxt) or gates.get(str(nxt))
    if not isinstance(gate, dict):
        out["blocker"] = f"missing_stage_gate_for_{nxt}"
        out["clauses"]["policy"] = {"ok": False, "reason": "missing_stage_gate"}
        return out

    sc = bot.get("promotion_scorecard") if isinstance(bot.get("promotion_scorecard"), dict) else {}
    gf = bot.get("governance_flags") if isinstance(bot.get("governance_flags"), dict) else {}
    sig = load_merged_eval_signals(bot)

    clauses: Dict[str, Any] = {}

    def _num(key: str, default: float = 0.0) -> float:
        v = sc.get(key)
        try:
            return float(v) if v is not None else default
        except Exception:
            return default

    def _int(key: str, default: int = 0) -> int:
        v = sc.get(key)
        try:
            return int(v) if v is not None else default
        except Exception:
            return default

    # --- Sample / performance thresholds ---
    clauses["min_shadow_trade_count"] = {
        "ok": _int("shadow_trade_count") >= int(gate.get("min_shadow_trade_count") or 0),
        "reason": "ok"
        if _int("shadow_trade_count") >= int(gate.get("min_shadow_trade_count") or 0)
        else f"need_{gate.get('min_shadow_trade_count')}_have_{_int('shadow_trade_count')}",
    }
    clauses["min_evaluation_count"] = {
        "ok": _int("evaluation_count") >= int(gate.get("min_evaluation_count") or 0),
        "reason": "ok"
        if _int("evaluation_count") >= int(gate.get("min_evaluation_count") or 0)
        else f"need_{gate.get('min_evaluation_count')}_have_{_int('evaluation_count')}",
    }
    clauses["min_sample_diversity_score"] = {
        "ok": _num("sample_diversity_score") >= float(gate.get("min_sample_diversity_score") or 0.0),
        "reason": "ok"
        if _num("sample_diversity_score") >= float(gate.get("min_sample_diversity_score") or 0.0)
        else "diversity_too_low",
    }
    clauses["min_expectancy"] = {
        "ok": _num("expectancy") >= float(gate.get("min_expectancy") or 0.0),
        "reason": "ok"
        if _num("expectancy") >= float(gate.get("min_expectancy") or 0.0)
        else "expectancy_too_low",
    }
    clauses["min_profit_factor"] = {
        "ok": _num("profit_factor") >= float(gate.get("min_profit_factor") or 0.0),
        "reason": "ok"
        if _num("profit_factor") >= float(gate.get("min_profit_factor") or 0.0)
        else "profit_factor_too_low",
    }
    clauses["max_drawdown_pct"] = {
        "ok": _num("max_drawdown_pct") <= float(gate.get("max_drawdown_pct") or 1e9),
        "reason": "ok"
        if _num("max_drawdown_pct") <= float(gate.get("max_drawdown_pct") or 1e9)
        else "drawdown_too_high",
    }
    clauses["max_avg_slippage_bps"] = {
        "ok": _num("avg_slippage_bps") <= float(gate.get("max_avg_slippage_bps") or 1e9),
        "reason": "ok"
        if _num("avg_slippage_bps") <= float(gate.get("max_avg_slippage_bps") or 1e9)
        else "slippage_too_high",
    }
    clauses["max_avg_latency_ms"] = {
        "ok": _num("avg_latency_ms") <= float(gate.get("max_avg_latency_ms") or 1e9),
        "reason": "ok"
        if _num("avg_latency_ms") <= float(gate.get("max_avg_latency_ms") or 1e9)
        else "latency_too_high",
    }
    clauses["truth_conflicts"] = {
        "ok": _int("truth_conflict_unresolved") <= int(gate.get("max_truth_conflict_unresolved") or 0),
        "reason": "ok"
        if _int("truth_conflict_unresolved") <= int(gate.get("max_truth_conflict_unresolved") or 0)
        else "unresolved_truth_conflicts",
    }
    clauses["duplicate_task_violations"] = {
        "ok": _int("duplicate_task_violations") <= int(gate.get("max_duplicate_task_violations") or 0),
        "reason": "ok"
        if _int("duplicate_task_violations") <= int(gate.get("max_duplicate_task_violations") or 0)
        else "duplicate_task_violations",
    }
    clauses["unauthorized_writes"] = {
        "ok": _int("unauthorized_writes") <= int(gate.get("max_unauthorized_writes") or 0),
        "reason": "ok"
        if _int("unauthorized_writes") <= int(gate.get("max_unauthorized_writes") or 0)
        else "unauthorized_writes",
    }
    clauses["min_promotion_readiness_score"] = {
        "ok": _num("promotion_readiness_score") >= float(gate.get("min_promotion_readiness_score") or 0.0),
        "reason": "ok"
        if _num("promotion_readiness_score") >= float(gate.get("min_promotion_readiness_score") or 0.0)
        else "readiness_score_too_low",
    }

    # Heartbeat freshness
    hb_age = _age_sec(str(bot.get("last_heartbeat_at") or ""))
    max_hb = float(gate.get("heartbeat_max_age_sec") or 1e9)
    clauses["heartbeat_fresh"] = {
        "ok": hb_age is not None and hb_age >= 0 and hb_age <= max_hb,
        "reason": "ok"
        if hb_age is not None and hb_age <= max_hb
        else ("missing_heartbeat" if hb_age is None else "stale_heartbeat"),
    }

    # Token budget (soft block — must not be negative)
    rem = bot.get("token_budget_remaining")
    try:
        rem_f = float(rem) if rem is not None else 1.0
    except Exception:
        rem_f = 0.0
    clauses["token_budget_non_negative"] = {"ok": rem_f >= 0.0, "reason": "ok" if rem_f >= 0.0 else "token_budget_exhausted"}

    # Governance flags
    need_ceo = bool(gate.get("require_ceo_review_pass"))
    need_risk = bool(gate.get("require_risk_review_pass"))
    clauses["ceo_review_pass"] = {
        "ok": (not need_ceo) or bool(gf.get("ceo_review_pass")),
        "reason": "ok" if (not need_ceo) or bool(gf.get("ceo_review_pass")) else "ceo_review_not_pass",
    }
    clauses["risk_review_pass"] = {
        "ok": (not need_risk) or bool(gf.get("risk_review_pass")),
        "reason": "ok" if (not need_risk) or bool(gf.get("risk_review_pass")) else "risk_review_not_pass",
    }

    # Cooldown since last auto-promotion
    lastp = _age_sec(str(bot.get("last_auto_promotion_at") or ""))
    cool = float(gate.get("promotion_cooldown_sec") or 0.0)
    if lastp is None:
        cool_ok = True
        cool_reason = "no_prior_promotion"
    else:
        cool_ok = lastp >= cool
        cool_reason = "ok" if cool_ok else f"cooldown_need_{cool}_sec_have_{int(lastp)}_sec"
    clauses["promotion_cooldown"] = {"ok": cool_ok, "reason": cool_reason}

    # Kill switch must be open (not frozen)
    ks = load_kill_switch()
    need_ks = bool(gate.get("require_kill_switch_open"))
    ks_ok = not bool(ks.get("orchestration_frozen")) if need_ks else True
    clauses["kill_switch_open"] = {
        "ok": ks_ok,
        "reason": "ok" if ks_ok else "orchestration_kill_switch_frozen",
    }

    # External evaluator signals (separate components — not self-certification)
    if require_sig:
        clauses["external_performance_evaluator"] = {
            "ok": bool(sig.get("performance_evaluator_ok")),
            "reason": "ok" if sig.get("performance_evaluator_ok") else "performance_evaluator_not_ok",
        }
        clauses["external_risk_engine"] = {
            "ok": bool(sig.get("risk_engine_ok")),
            "reason": "ok" if sig.get("risk_engine_ok") else "risk_engine_not_ok",
        }
        clauses["external_truth_layer"] = {
            "ok": bool(sig.get("truth_layer_ok")),
            "reason": "ok" if sig.get("truth_layer_ok") else "truth_layer_not_ok",
        }
        clauses["external_orchestration_policy"] = {
            "ok": bool(sig.get("orchestration_policy_ok")),
            "reason": "ok" if sig.get("orchestration_policy_ok") else "orchestration_policy_not_ok",
        }
    else:
        clauses["external_eval_signals_required"] = {
            "ok": True,
            "reason": "policy_require_external_eval_signals_false",
        }

    # Demotion / disable guards
    clauses["not_demotion_risk"] = {
        "ok": bot.get("demotion_risk") is not True,
        "reason": "ok" if bot.get("demotion_risk") is not True else "demotion_risk_set",
    }
    st = str(bot.get("status") or "")
    clauses["status_active_like"] = {
        "ok": st in ("", "active", "pending_review"),
        "reason": "ok" if st in ("", "active", "pending_review") else f"bad_status_{st}",
    }

    out["clauses"] = clauses
    all_ok = all(bool(v.get("ok")) for v in clauses.values() if isinstance(v, dict))
    out["passed"] = all_ok
    if not all_ok:
        for k, v in clauses.items():
            if isinstance(v, dict) and not v.get("ok"):
                out["blocker"] = f"{k}:{v.get('reason')}"
                break
    else:
        out["blocker"] = None
    return out


def apply_tier_update_to_bot_record(bot: Dict[str, Any], new_tier: str) -> Dict[str, Any]:
    """Return updated bot dict with permission_level + capabilities for ``new_tier``."""
    from trading_ai.global_layer.lock_layer.promotion_rung import assert_no_rung_skip, sync_execution_rung_on_bot

    b = dict(bot)
    tier = str(new_tier).strip().upper()
    prev = str(b.get("promotion_tier") or PromotionTier.T0.value)
    ok_skip, why = assert_no_rung_skip(prev, tier)
    if not ok_skip and why.startswith("skip_forbidden"):
        raise ValueError(why)
    pl, caps = permission_and_capabilities_for_promotion_tier(tier)
    b["promotion_tier"] = tier
    b["promotion_target_tier"] = _next_tier(tier) or tier
    b["permission_level"] = pl
    b["promotion_capabilities"] = caps
    b["last_auto_promotion_at"] = _iso()
    b["orchestration_lifecycle"] = {
        "T0": "shadow",
        "T1": "advisory",
        "T2": "advisory",
        "T3": "candidate",
        "T4": "promoted_lane",
        "T5": "route_primary",
    }.get(tier, b.get("orchestration_lifecycle") or "shadow")
    b = sync_execution_rung_on_bot(b)
    # Eligibility for capital tiers is handled by capital governor — do not grant capital here.
    return b


def fast_track_eligible(bot: Dict[str, Any], policy: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """Optional deterministic skip-ahead — disabled unless policy + bot evidence."""
    pol = policy or load_promotion_contract_policy()
    ft = pol.get("fast_track") or {}
    if not bool(ft.get("enabled")):
        return False, "fast_track_disabled"
    sc = bot.get("promotion_scorecard") if isinstance(bot.get("promotion_scorecard"), dict) else {}
    cycles = int(sc.get("clean_live_cycles") or 0)
    pf = float(sc.get("profit_factor") or 0.0)
    conflicts = int(sc.get("truth_conflict_unresolved") or 0)
    if cycles < 500:
        return False, "fast_track_clean_cycles"
    if pf < 2.0:
        return False, "fast_track_profit_factor"
    if conflicts > 0:
        return False, "fast_track_conflicts"
    return True, "ok"


def max_skip_target_tier(bot: Dict[str, Any], policy: Optional[Dict[str, Any]] = None) -> str:
    """If fast-track fires, allow jumping to T3 when currently T0/T1 (still deterministic)."""
    ok, _ = fast_track_eligible(bot, policy=policy)
    if not ok:
        return str(bot.get("promotion_tier") or PromotionTier.T0.value)
    cur = promotion_tier_index(str(bot.get("promotion_tier") or "T0"))
    if cur <= 1:
        return "T3"
    return str(bot.get("promotion_tier") or PromotionTier.T0.value)
