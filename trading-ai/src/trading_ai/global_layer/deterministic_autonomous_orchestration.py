"""
Auto-promotion and capital scale cycles — deterministic; writes canonical truth artifacts.

Call from CLI, CEO pipeline, or scheduled runner. Does not place venue orders.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.global_layer.bot_registry import load_registry, patch_bot
from trading_ai.global_layer.capital_governor import (
    apply_capital_tier,
    evaluate_capital_scale_up,
    load_capital_governor_policy,
    maybe_scale_capital_after_promotion,
    sync_registry_from_bot,
    write_capital_readiness_truth,
)
from trading_ai.global_layer.execution_authority import get_holder
from trading_ai.global_layer.orchestration_paths import bot_permissions_matrix_path, bot_system_readiness_path
from trading_ai.global_layer.lock_layer.truth_writers import CANONICAL_WRITER_IDS, TruthDomain, finalize_promotion_cycle_truth
from trading_ai.global_layer.promotion_contract_engine import (
    apply_tier_update_to_bot_record,
    evaluate_promotion_contract_detailed,
    fast_track_eligible,
    load_promotion_contract_policy,
    max_skip_target_tier,
)
from trading_ai.global_layer.orchestration_schema import promotion_tier_index


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def _next_tier_name(cur: str) -> str:
    i = promotion_tier_index(cur)
    if i >= 5:
        return cur
    return f"T{i + 1}"


def run_auto_promotion_cycle(*, registry_path: Optional[Path] = None, policy_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    For each bot: advance promotion tier while contract passes (one step by default; multi-step when fast-track enabled).
    """
    reg = load_registry(registry_path)
    pol = load_promotion_contract_policy(path=policy_path) if policy_path else load_promotion_contract_policy()
    results: List[Dict[str, Any]] = []

    for bot in reg.get("bots") or []:
        bid = str(bot.get("bot_id") or "")
        b = dict(bot)
        ft_enabled = bool((pol.get("fast_track") or {}).get("enabled"))
        ft_ok = fast_track_eligible(b, policy=pol)[0] if ft_enabled else False
        max_tier_cap = max_skip_target_tier(b, policy=pol) if ft_ok else _next_tier_name(str(b.get("promotion_tier") or "T0"))

        steps = 0
        last_ev: Dict[str, Any] = {}
        while True:
            last_ev = evaluate_promotion_contract_detailed(b, policy=pol)
            if not last_ev.get("passed") or not last_ev.get("target_tier"):
                break
            tgt = str(last_ev["target_tier"])
            if ft_ok and promotion_tier_index(tgt) > promotion_tier_index(max_tier_cap):
                break
            b = apply_tier_update_to_bot_record(b, tgt)
            patch_bot(bid, b, path=registry_path)
            maybe_scale_capital_after_promotion(b, policy=load_capital_governor_policy())
            steps += 1
            if not ft_ok:
                break
            if promotion_tier_index(str(b.get("promotion_tier"))) >= promotion_tier_index(max_tier_cap):
                break
            if steps >= 5:
                break

        results.append(
            {
                "bot_id": bid,
                "promotion_steps": steps,
                "fast_track": ft_ok,
                "last_evaluation": last_ev,
            }
        )

    truth = {
        "truth_version": "bot_auto_promotion_truth_v1",
        "generated_at": _iso(),
        "policy_truth_version": pol.get("truth_version"),
        "results": results,
    }
    finalize_promotion_cycle_truth(truth, writer_id=CANONICAL_WRITER_IDS[TruthDomain.PROMOTION])
    _refresh_permissions_matrix_artifact(registry_path)
    _refresh_system_readiness(registry_path)
    return truth


def run_capital_scale_up_cycle(*, registry_path: Optional[Path] = None) -> Dict[str, Any]:
    """Apply at most one capital tier step-up per bot when scale-up contract passes."""
    reg = load_registry(registry_path)
    applied: List[Dict[str, Any]] = []
    for bot in reg.get("bots") or []:
        bid = str(bot.get("bot_id") or "")
        ev = evaluate_capital_scale_up(dict(bot))
        if ev.get("allowed") and str(ev.get("target_tier")) != str(bot.get("capital_authority_tier")):
            nb = apply_capital_tier(
                dict(bot),
                str(ev["target_tier"]),
                "scale_up_contract_satisfied",
                "capital_governor_cycle",
            )
            patch_bot(bid, nb, path=registry_path)
            sync_registry_from_bot(nb)
            applied.append({"bot_id": bid, "to": ev["target_tier"]})
    out = {"truth_version": "capital_scale_up_cycle_v1", "generated_at": _iso(), "applied": applied}
    write_capital_readiness_truth(extra=out)
    return out


def run_full_deterministic_cycle(*, registry_path: Optional[Path] = None) -> Dict[str, Any]:
    """Promotion first, then capital scale-up."""
    p = run_auto_promotion_cycle(registry_path=registry_path)
    c = run_capital_scale_up_cycle(registry_path=registry_path)
    return {"promotion": p, "capital_scale_up": c, "generated_at": _iso()}


def _refresh_permissions_matrix_artifact(registry_path: Optional[Path] = None) -> None:
    reg = load_registry(registry_path)
    matrix = {"truth_version": "bot_permissions_matrix_v1", "generated_at": _iso(), "rows": []}
    for b in reg.get("bots") or []:
        matrix["rows"].append(
            {
                "bot_id": b.get("bot_id"),
                "promotion_tier": b.get("promotion_tier"),
                "permission_level": b.get("permission_level"),
                "capital_authority_tier": b.get("capital_authority_tier"),
                "capabilities": b.get("promotion_capabilities"),
            }
        )
    _write_json(bot_permissions_matrix_path(), matrix)


def _refresh_system_readiness(registry_path: Optional[Path] = None) -> None:
    reg = load_registry(registry_path)
    load_capital_governor_policy()
    ready = {
        "truth_version": "bot_system_readiness_v1",
        "generated_at": _iso(),
        "bot_count": len(reg.get("bots") or []),
        "honesty": "Promotion and capital are separate; live orders require execution slot + venue guards.",
    }
    _write_json(bot_system_readiness_path(), ready)


def assert_single_live_capital_consumer(
    avenue: str,
    gate: str,
    route: str,
    bot_id: str,
    *,
    registry_path: Optional[Path] = None,
) -> Tuple[bool, str]:
    """
    Hard guard: live capital consumption must align with execution authority holder for the route.
    """
    h = get_holder(avenue, gate, route)
    if not h:
        return False, "no_execution_authority_holder"
    if str(h.get("bot_id")) != str(bot_id):
        return False, "execution_authority_holder_mismatch"
    return True, "ok"
