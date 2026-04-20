"""Deterministic links between execution registry, hierarchy registry, and orchestration (advisory)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.global_layer.bot_hierarchy.paths import default_bot_hierarchy_root
from trading_ai.global_layer.bot_hierarchy.registry import list_bots, load_hierarchy_state
from trading_ai.global_layer.bot_registry import load_registry
from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.storage.storage_adapter import LocalStorageAdapter


def build_registry_cross_link_report(
    *,
    runtime_root: Optional[Path] = None,
    hierarchy_root: Optional[Path] = None,
    registry_path: Optional[Path] = None,
) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    hroot = Path(hierarchy_root).resolve() if hierarchy_root is not None else default_bot_hierarchy_root()
    reg = load_registry(registry_path)
    st = load_hierarchy_state(hroot)
    bots = list_bots(path=hroot)

    orch_bots = [b for b in (reg.get("bots") or []) if isinstance(b, dict)]
    hierarchy_by_id = {b.bot_id: b for b in bots}

    links: List[Dict[str, Any]] = []
    for ob in orch_bots:
        bid = str(ob.get("bot_id") or "")
        hb = hierarchy_by_id.get(bid)
        links.append(
            {
                "orchestration_bot_id": bid,
                "linked_hierarchy_bot": hb.model_dump(mode="json") if hb else None,
                "link_status": "linked" if hb else "unlinked_optional",
                "hierarchy_fields_if_missing": (
                    "Hierarchy bots are intelligence-layer; orchestration ids may differ — see linked_orchestration_bot_id on hierarchy records."
                ),
            }
        )

    for hb in bots:
        if hb.linked_orchestration_bot_id:
            links.append(
                {
                    "hierarchy_bot_id": hb.bot_id,
                    "linked_orchestration_bot_id": hb.linked_orchestration_bot_id,
                    "link_status": "explicit_reverse_link",
                }
            )

    out = {
        "truth_version": "registry_cross_link_v1",
        "runtime_root": str(root),
        "orchestration_bot_count": len(orch_bots),
        "hierarchy_bot_count": len(bots),
        "gate_candidates_count": len(st.get("gate_candidates") or []),
        "links": links[:500],
        "honesty": "Unlinked status is explicit — not all hierarchy roles exist in orchestration registry by design.",
    }
    ad = LocalStorageAdapter(runtime_root=root)
    ad.write_json("data/control/registry_cross_link_truth.json", out)
    return out
