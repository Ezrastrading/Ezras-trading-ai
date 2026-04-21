"""
Deployable Coinbase capital split Gate A / Gate B — policy artifact only (fail-closed if input unknown).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.runtime_paths import resolve_ezras_runtime_root_for_daemon_authority
from trading_ai.storage.storage_adapter import LocalStorageAdapter

_REL = "data/control/coinbase_gate_capital_split_policy.json"
_SNAPSHOT = "data/control/coinbase_gate_capital_split_snapshot.json"


def default_split_policy() -> Dict[str, Any]:
    return {
        "truth_version": "coinbase_gate_capital_split_policy_v1",
        "gate_a_fraction": 0.5,
        "gate_b_fraction": 0.5,
        "honesty": "Applies to honestly computed deployable_usd only — not a bypass of governors.",
    }


def load_split_policy(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = resolve_ezras_runtime_root_for_daemon_authority(runtime_root)
    p = root / _REL
    if p.is_file():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
        except (OSError, json.JSONDecodeError):
            pass
    return default_split_policy()


def compute_coinbase_gate_capital_split(
    deployable_usd: Optional[float],
    *,
    runtime_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Returns split snapshot; if ``deployable_usd`` is None or NaN, fail-closed (no fake allocation).
    """
    pol = load_split_policy(runtime_root=runtime_root)
    root = resolve_ezras_runtime_root_for_daemon_authority(runtime_root)
    fa = float(pol.get("gate_a_fraction") or 0.5)
    fb = float(pol.get("gate_b_fraction") or 0.5)
    ok = (
        deployable_usd is not None
        and deployable_usd == deployable_usd
        and deployable_usd >= 0
        and abs(fa + fb - 1.0) < 1e-6
    )
    if not ok:
        out = {
            "truth_version": "coinbase_gate_capital_split_snapshot_v1",
            "ok": False,
            "failure_reason": "deployable_usd_not_computable_or_fractions_invalid",
            "deployable_usd_input": deployable_usd,
            "policy": pol,
        }
    else:
        d = float(deployable_usd or 0.0)
        out = {
            "truth_version": "coinbase_gate_capital_split_snapshot_v1",
            "ok": True,
            "deployable_usd": d,
            "gate_a_usd": round(d * fa, 6),
            "gate_b_usd": round(d * fb, 6),
            "gate_a_fraction": fa,
            "gate_b_fraction": fb,
            "policy": pol,
        }
    ad = LocalStorageAdapter(runtime_root=root)
    ad.write_json(_SNAPSHOT, out)
    ad.write_text(_SNAPSHOT.replace(".json", ".txt"), json.dumps(out, indent=2, default=str) + "\n")
    return out
