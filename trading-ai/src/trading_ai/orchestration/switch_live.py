"""Authoritative live switch evaluation per avenue (conservative; B/C default false)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

from trading_ai.control.system_execution_lock import load_system_execution_lock, require_live_execution_allowed
from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.safety.failsafe_guard import load_failsafe_state, load_kill_switch
from trading_ai.storage.storage_adapter import LocalStorageAdapter


def _operator_confirmed(*, runtime_root: Path) -> Tuple[bool, str]:
    env_ok = (os.environ.get("EZRAS_OPERATOR_LIVE_CONFIRMED") or "").strip().lower() in ("1", "true", "yes")
    if env_ok:
        return True, "env_EZRAS_OPERATOR_LIVE_CONFIRMED"
    p = runtime_root / "data" / "control" / "operator_live_confirmation.json"
    if not p.is_file():
        return False, "missing_operator_live_confirmation_json"
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and raw.get("confirmed") is True:
            return True, "file_operator_live_confirmation"
    except (json.JSONDecodeError, OSError):
        pass
    return False, "operator_not_confirmed"


def _independent_live_proof(
    avenue_id: str,
    *,
    runtime_root: Path,
) -> Tuple[bool, str]:
    """B/C may only go live if an explicit independent proof artifact says so (not inherited from A)."""
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    path = f"data/control/avenue_{avenue_id}_independent_live_proof.json"
    raw = ad.read_json(path)
    if not raw:
        return False, f"missing_{path}"
    if raw.get("independent_live_proven") is True and raw.get("validated_by_operator") is True:
        return True, path
    return False, "independent_live_proof_insufficient"


def compute_avenue_switch_live_now(
    avenue_id: str,
    *,
    runtime_root: Path | None = None,
) -> Tuple[bool, List[str], Dict[str, Any]]:
    """
    Return (allowed, blockers, diagnostics).

    **Policy this pass:** only Avenue ``A`` may become true without a bespoke B/C proof file.
    Avenues ``B`` and ``C`` default to false unless their independent proof artifact is complete.
    """
    aid = str(avenue_id or "").strip().upper()
    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    blockers: List[str] = []
    diag: Dict[str, Any] = {"avenue_id": aid, "runtime_root": str(root)}

    if load_kill_switch(runtime_root=root):
        blockers.append("system_kill_switch_active")

    st = load_failsafe_state(runtime_root=root)
    if st.get("halted"):
        blockers.append("failsafe_halted")

    lock = load_system_execution_lock(runtime_root=root)
    if not bool(lock.get("system_locked")):
        blockers.append("system_execution_unlocked")
    if not bool(lock.get("ready_for_live_execution")):
        blockers.append("ready_for_live_execution_false")

    if aid == "A":
        ok_g, reason_g = require_live_execution_allowed("gate_a", runtime_root=root)
        if not ok_g:
            blockers.append(f"gate_execution_lock:{reason_g}")
        if not bool(lock.get("gate_a_enabled")):
            blockers.append("gate_a_disabled_in_lock")

        op_ok, op_src = _operator_confirmed(runtime_root=root)
        diag["operator_confirmation_source"] = op_src
        strict = (os.environ.get("EZRAS_REQUIRE_OPERATOR_CONFIRMATION") or "1").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        if strict and not op_ok:
            blockers.append(op_src)

        ad = LocalStorageAdapter(runtime_root=root)
        gng = ad.read_json("data/control/go_no_go_decision.json")
        if isinstance(gng, dict) and gng.get("ready_for_first_5_trades") is False:
            blockers.append("go_no_go_not_ready_for_first_5_trades")

        mirror = ad.read_json("data/control/execution_mirror_results.json")
        if mirror is not None and mirror.get("ok") is False:
            blockers.append("execution_mirror_failed")

        allowed = len(blockers) == 0
        diag["policy"] = "avenue_a_only_if_all_truth_artifacts_align"
        return allowed, blockers, diag

    if aid in ("B", "C"):
        proof_ok, proof_reason = _independent_live_proof(aid, runtime_root=root)
        diag["independent_proof_check"] = proof_reason
        if not proof_ok:
            blockers.append(f"avenue_{aid}_requires_independent_live_proof")
        if aid == "B":
            ok_b, r_b = require_live_execution_allowed("gate_b", runtime_root=root)
            if not ok_b:
                blockers.append(f"gate_b_lock:{r_b}")
            if not bool(lock.get("gate_b_enabled")):
                blockers.append("gate_b_disabled")
        if aid == "C":
            blockers.append("avenue_c_tastytrade_scaffold_execution_not_wired")

        op_ok, op_src = _operator_confirmed(runtime_root=root)
        diag["operator_confirmation_source"] = op_src
        strict = (os.environ.get("EZRAS_REQUIRE_OPERATOR_CONFIRMATION") or "1").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        if strict and not op_ok:
            blockers.append(op_src)

        allowed = len(blockers) == 0
        diag["policy"] = "avenue_b_c_default_false_unless_independent_proof_and_locks"
        return allowed, blockers, diag

    blockers.append("unknown_avenue_id")
    return False, blockers, diag
