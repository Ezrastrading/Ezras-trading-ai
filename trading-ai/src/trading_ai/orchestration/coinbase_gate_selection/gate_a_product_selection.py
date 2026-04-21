"""
Gate A product selection — deterministic priority (BTC/ETH first), policy file, measurable scores.

Does not authorize live trading; produces a snapshot for proof / debugging only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.nte.hardening.coinbase_product_policy import coinbase_product_nte_allowed, ordered_validation_candidates
from trading_ai.runtime_paths import resolve_ezras_runtime_root_for_daemon_authority

_POLICY_NAME = "gate_a_product_selection_policy.json"
_SNAPSHOT = "data/control/gate_a_selection_snapshot.json"


def _default_policy() -> Dict[str, Any]:
    p = Path(__file__).resolve().parent / "default_gate_a_policy.json"
    return json.loads(p.read_text(encoding="utf-8"))


def load_gate_a_product_policy(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = resolve_ezras_runtime_root_for_daemon_authority(runtime_root)
    ctrl = root / "data" / "control" / _POLICY_NAME
    if ctrl.is_file():
        try:
            raw = json.loads(ctrl.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
        except (OSError, json.JSONDecodeError):
            pass
    return _default_policy()


def _spread_bps(mid: float, bid: float, ask: float) -> Optional[float]:
    if mid <= 0 or bid <= 0 or ask <= 0:
        return None
    return (ask - bid) / mid * 10000.0


def score_gate_a_candidate(
    *,
    product_id: str,
    bid: float,
    ask: float,
    mid: float,
    policy: Dict[str, Any],
) -> Tuple[float, List[str], List[str]]:
    """Higher is better. Uses spread tightness only when bid/ask/mid present; else neutral."""
    why_ok: List[str] = []
    why_bad: List[str] = []
    max_spread = float(policy.get("max_spread_bps") or 50)
    sp = _spread_bps(mid, bid, ask)
    if sp is None:
        score = 50.0
        why_ok.append("no_spread_data_neutral_score")
    else:
        if sp > max_spread:
            why_bad.append(f"spread_bps_{sp:.2f}_exceeds_max_{max_spread}")
            return -1000.0, why_ok, why_bad
        score = max(0.0, 100.0 - sp)
        why_ok.append(f"spread_bps_{sp:.2f}")

    pri = [str(x).strip().upper() for x in (policy.get("priority_products") or []) if str(x).strip()]
    if product_id.upper() in pri:
        bonus = 25.0 * float(len(pri) - pri.index(product_id.upper())) / float(len(pri) or 1)
        score += bonus
        why_ok.append("priority_tier_bonus")

    deny = {str(x).strip().upper() for x in (policy.get("deny_products") or []) if str(x).strip()}
    if product_id.upper() in deny:
        why_bad.append("deny_list")
        return -2000.0, why_ok, why_bad

    allow = [str(x).strip().upper() for x in (policy.get("allow_products") or []) if str(x).strip()]
    if allow and product_id.upper() not in allow:
        why_bad.append("not_in_allow_list")
        return -500.0, why_ok, why_bad

    return score, why_ok, why_bad


def run_gate_a_product_selection(
    *,
    runtime_root: Path,
    client: Any,
    quote_usd: float,
    explicit_product_id: Optional[str] = None,
    anchored_majors_only: bool = False,
) -> Dict[str, Any]:
    """
    Select Gate A product. If ``explicit_product_id`` set (not AUTO), returns it with source operator_explicit.

    Writes ``data/control/gate_a_selection_snapshot.json`` on success.
    """
    root = resolve_ezras_runtime_root_for_daemon_authority(runtime_root)
    policy = load_gate_a_product_policy(runtime_root=root)

    if explicit_product_id and str(explicit_product_id).strip().upper() not in ("", "AUTO", "AUTO_SELECT"):
        pid = str(explicit_product_id).strip().upper()
        if not coinbase_product_nte_allowed(pid):
            snap = {
                "truth_version": "gate_a_selection_snapshot_v1",
                "selected_product": None,
                "selected_product_source": "operator_explicit_rejected",
                "selection_failure_code": "product_not_nte_allowed",
                "selection_failure_reason": f"{pid} not in NTE products allowlist",
                "candidate_rankings": [],
                "policy": policy,
            }
            _write_snap(root, snap)
            return snap

        snap = {
            "truth_version": "gate_a_selection_snapshot_v1",
            "selected_product": pid,
            "selected_product_source": "operator_explicit",
            "candidate_rankings": [],
            "why_selected": ["operator_supplied_product_id"],
            "why_rejected": [],
            "policy": policy,
        }
        _write_snap(root, snap)
        return snap

    # Merge priority with NTE candidates
    pri = [str(x).strip().upper() for x in (policy.get("priority_products") or [])]
    rest = [x for x in ordered_validation_candidates() if x.upper() not in pri]
    candidates = list(dict.fromkeys(pri + rest))[: int(policy.get("max_candidate_count") or 12)]

    if anchored_majors_only:
        allow_quotes = {
            str(x).strip().upper()
            for x in (policy.get("quote_currency_allow") or [])
            if str(x).strip()
        }
        if not allow_quotes:
            allow_quotes = {"USD"}

        approved_bases = {
            str(x).strip().upper()
            for x in (policy.get("approved_base_assets") or [])
            if str(x).strip()
        }
        deny_bases = {
            str(x).strip().upper()
            for x in (policy.get("deny_base_assets") or [])
            if str(x).strip()
        }
        if deny_bases:
            approved_bases = {b for b in approved_bases if b not in deny_bases}

        def _gate_a_conservative_universe(pid: str) -> bool:
            """
            Gate A "anchored majors" mode is *not* BTC-only.

            It means:
            - conservative approved base asset universe (still capped by NTE allowlist)
            - conservative quote currency allowlist
            - runtime quote/inventory preflight later must align chosen == preferred
            """
            u = str(pid or "").strip().upper()
            if "-" not in u:
                return False
            base, quote = u.split("-", 1)
            if quote not in allow_quotes:
                return False
            if deny_bases and base in deny_bases:
                return False
            if approved_bases and base not in approved_bases:
                return False
            return coinbase_product_nte_allowed(u)

        candidates = [c for c in candidates if _gate_a_conservative_universe(c)]
        if not candidates:
            snap = {
                "truth_version": "gate_a_selection_snapshot_v1",
                "selected_product": None,
                "selected_product_source": "selection_failed",
                "selection_failure_code": "no_gate_a_conservative_candidates",
                "selection_failure_reason": (
                    "anchored_majors_only_enabled_but_no_candidates_passed_gate_a_conservative_universe_filters"
                ),
                "candidate_rankings": [],
                "policy": policy,
                "anchored_majors_only": True,
            }
            _write_snap(root, snap)
            return snap

    rankings: List[Dict[str, Any]] = []
    for pid in candidates:
        if not coinbase_product_nte_allowed(pid):
            rankings.append(
                {
                    "product_id": pid,
                    "score": -1e6,
                    "passed": False,
                    "why_rejected": ["not_nte_allowed"],
                }
            )
            continue
        bid = ask = mid = 0.0
        try:
            from trading_ai.shark.outlets.coinbase import _brokerage_public_request

            j = _brokerage_public_request(f"/market/products/{pid}/ticker")
            if isinstance(j, dict):
                bid = float(j.get("bid") or j.get("best_bid") or 0)
                ask = float(j.get("ask") or j.get("best_ask") or 0)
                mid = float(j.get("price") or j.get("last") or 0) or ((bid + ask) / 2.0 if bid and ask else 0.0)
        except Exception as exc:
            rankings.append(
                {
                    "product_id": pid,
                    "score": -1.0,
                    "passed": False,
                    "why_rejected": [f"ticker_error:{type(exc).__name__}"],
                }
            )
            continue

        sc, ok, bad = score_gate_a_candidate(product_id=pid, bid=bid, ask=ask, mid=mid, policy=policy)
        rankings.append(
            {
                "product_id": pid,
                "score": sc,
                "passed": sc > -100.0,
                "why_ok": ok,
                "why_rejected": bad,
            }
        )

    viable = [r for r in rankings if r.get("passed") and float(r.get("score") or 0) > -500]
    if not viable:
        snap = {
            "truth_version": "gate_a_selection_snapshot_v1",
            "selected_product": None,
            "selected_product_source": "selection_failed",
            "selection_failure_code": "no_viable_product_after_filters",
            "selection_failure_reason": "All candidates failed NTE, spread, or allow/deny policy",
            "candidate_rankings": rankings,
            "policy": policy,
        }
        _write_snap(root, snap)
        return snap

    # When anchored majors mode is enabled (autonomous Avenue A default), prefer a selection that is
    # actually executable under runtime quote/inventory resolution — not merely best on public tickers.
    preflight_rows: List[Dict[str, Any]] = []
    pid_best: Optional[str] = None
    best: Optional[Dict[str, Any]] = None
    if anchored_majors_only:
        pri_rank = {p: i for i, p in enumerate(pri)}
        viable.sort(
            key=lambda r: (
                -float(r.get("score") or 0.0),
                int(pri_rank.get(str(r.get("product_id") or "").strip().upper(), 999)),
            )
        )
        try:
            from trading_ai.runtime_proof.coinbase_accounts import preflight_exact_spot_product
        except Exception as exc:
            snap = {
                "truth_version": "gate_a_selection_snapshot_v1",
                "selected_product": None,
                "selected_product_source": "selection_failed",
                "selection_failure_code": "quote_preflight_import_failed",
                "selection_failure_reason": str(exc),
                "candidate_rankings": rankings,
                "policy": policy,
                "anchored_majors_only": True,
            }
            _write_snap(root, snap)
            return snap

        for cand in viable:
            pid_try = str(cand.get("product_id") or "").strip().upper()
            if not pid_try:
                continue
            try:
                ok, qd, qerr = preflight_exact_spot_product(
                    client,
                    product_id=pid_try,
                    quote_notional=float(quote_usd),
                    runtime_root=root,
                )
            except Exception as exc:
                preflight_rows.append({"product_id": pid_try, "ok": False, "error": f"{type(exc).__name__}:{exc}"})
                continue
            aligned = bool(ok) and (not bool(qerr))
            row = {
                "product_id": pid_try,
                "ok": aligned,
                "quote_err": qerr,
                "chosen_product": pid_try if aligned else None,
                "preferred_vs_chosen_aligned": aligned,
                "quote_diagnostics": qd,
            }
            preflight_rows.append(row)
            if aligned:
                pid_best = pid_try
                best = cand
                break

        if not pid_best or best is None:
            snap = {
                "truth_version": "gate_a_selection_snapshot_v1",
                "selected_product": None,
                "selected_product_source": "selection_failed",
                "selection_failure_code": "no_executable_product_after_quote_preflight",
                "selection_failure_reason": "No anchored-major candidate passed runtime quote resolution for requested notional",
                "candidate_rankings": rankings,
                "quote_usd_request": float(quote_usd),
                "policy": policy,
                "anchored_majors_only": True,
                "quote_preflight_attempts": preflight_rows,
                "honesty": "Anchored-major autonomous selection includes runtime quote/inventory preflight — public ticker score alone is not sufficient.",
            }
            _write_snap(root, snap)
            return snap
    else:
        viable.sort(key=lambda r: float(r.get("score") or 0.0), reverse=True)
        best = viable[0]
        pid_best = str(best["product_id"])

    snap = {
        "truth_version": "gate_a_selection_snapshot_v1",
        "selected_product": pid_best,
        "selected_product_source": "gate_a_selection_engine",
        "why_selected": (best.get("why_ok") or []) + (["runtime_quote_preflight_ok"] if anchored_majors_only else []),
        "why_rejected": [{"product_id": r["product_id"], "reasons": r.get("why_rejected") or []} for r in rankings if r.get("why_rejected")],
        "candidate_rankings": rankings,
        "quote_usd_request": float(quote_usd),
        "policy": policy,
        "anchored_majors_only": bool(anchored_majors_only),
        **({"quote_preflight_attempts": preflight_rows} if anchored_majors_only else {}),
        "honesty": "Scores use public ticker spread + priority bonus — not profitability claims.",
    }
    _write_snap(root, snap)
    return snap


def _write_snap(root: Path, snap: Dict[str, Any]) -> None:
    from trading_ai.storage.storage_adapter import LocalStorageAdapter

    ad = LocalStorageAdapter(runtime_root=root)
    ad.write_json(_SNAPSHOT, snap)
    ad.write_text(_SNAPSHOT.replace(".json", ".txt"), json.dumps(snap, indent=2, default=str) + "\n")
