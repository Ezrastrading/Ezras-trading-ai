"""Strategy/edge/gate/venue/instrument comparisons — explicit evidence tier for deltas."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.intelligence.edge_research.artifacts import comparisons_dir
from trading_ai.intelligence.edge_research.models import ResearchComparisonRecord, ResearchStatus, new_research_record_id
from trading_ai.intelligence.edge_research.registry import load_registry, merge_comparisons
from trading_ai.intelligence.edge_research.scoring import score_record


def _infer_tier_from_records(left: Dict[str, Any], right: Dict[str, Any]) -> str:
    """Max evidence tier between two rows — conservative."""
    tiers = [ResearchStatus.hypothesis.value, ResearchStatus.under_research.value, ResearchStatus.mock_supported.value, ResearchStatus.staged_supported.value, ResearchStatus.live_supported.value]
    def tier_idx(st: str) -> int:
        st = st or ResearchStatus.hypothesis.value
        return tiers.index(st) if st in tiers else 0
    ls = str(left.get("current_status") or "")
    rs = str(right.get("current_status") or "")
    m = max(tier_idx(ls), tier_idx(rs))
    return tiers[m]


def build_pair_comparison(left: Dict[str, Any], right: Dict[str, Any], *, dimension: str) -> ResearchComparisonRecord:
    left_id = str(left.get("record_id") or "")
    right_id = str(right.get("record_id") or "")
    stable_cmp_id = "ercmp__" + "__".join(sorted([left_id, right_id])) if left_id and right_id else new_research_record_id("ercmp")

    sl = score_record(left)
    sr = score_record(right)
    better = "left" if sl > sr else "right" if sr > sl else "tie"
    tier = _infer_tier_from_records(left, right)
    diff_tier = "hypothesis_only"
    if tier in (ResearchStatus.mock_supported.value,):
        diff_tier = "mock_supported"
    elif tier == ResearchStatus.staged_supported.value:
        diff_tier = "staged_supported"
    elif tier == ResearchStatus.live_supported.value:
        diff_tier = "live_supported"

    why = ""
    if better == "left":
        why = f"Left scores higher under current evidence ({sl:.3f} vs {sr:.3f})."
    elif better == "right":
        why = f"Right scores higher under current evidence ({sr:.3f} vs {sl:.3f})."
    else:
        why = "Comparable scores — need more scoped tests."

    return ResearchComparisonRecord(
        record_id=stable_cmp_id,
        dimension=dimension,
        left_record_id=left_id,
        right_record_id=right_id,
        left_label=str(left.get("strategy_name") or left.get("edge_name") or left_id),
        right_label=str(right.get("strategy_name") or right.get("edge_name") or right_id),
        avenue_id=str(left.get("avenue_id") or "") or str(right.get("avenue_id") or ""),
        gate_id=str(left.get("gate_id") or "") or str(right.get("gate_id") or ""),
        why_one_is_better=why,
        where_one_is_better="Higher score implies better fit only under matching scope keys (avenue/gate/venue).",
        where_one_fails="See conditions_where_it_fails on each record; cross-avenue failure is common.",
        confidence=min(1.0, (float(left.get("confidence") or 0) + float(right.get("confidence") or 0)) / 2),
        evidence_count=len(set((left.get("supporting_artifact_paths") or []) + (right.get("supporting_artifact_paths") or []))),
        difference_evidence_tier=diff_tier,
        venue=str(left.get("venue") or right.get("venue") or ""),
        market_type=str(left.get("market_type") or right.get("market_type") or ""),
        instrument_type=str(left.get("instrument_type") or right.get("instrument_type") or ""),
        supporting_artifact_paths=list(
            set((left.get("supporting_artifact_paths") or []) + (right.get("supporting_artifact_paths") or []))
        ),
        operator_plain_english_summary=f"Comparison on {dimension}: {why} Evidence tier for delta: {diff_tier}.",
    )


def run_pairwise_comparisons(*, runtime_root: Optional[Path] = None, max_pairs: int = 40) -> Dict[str, Any]:
    reg = load_registry(runtime_root=runtime_root)
    recs = [r for r in (reg.get("records") or []) if isinstance(r, dict)]
    comparisons: List[Dict[str, Any]] = []
    count = 0
    for i, a in enumerate(recs):
        for b in recs[i + 1 :]:
            if count >= max_pairs:
                break
            av_a, av_b = str(a.get("avenue_id") or ""), str(b.get("avenue_id") or "")
            if av_a and av_b and av_a != av_b:
                continue
            cmp_rec = build_pair_comparison(a, b, dimension="scoped_pair")
            comparisons.append(cmp_rec.to_json_dict())
            count += 1
        if count >= max_pairs:
            break
    merge_comparisons(comparisons, runtime_root=runtime_root)
    gpath = comparisons_dir(runtime_root=runtime_root) / "global_comparisons.json"
    gpath.write_text(
        json.dumps(
            {
                "artifact": "global_comparisons",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "comparisons": comparisons,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    return {"status": "ok", "comparison_count": len(comparisons), "path": str(gpath)}


def update_best_rankings(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """Write best_edges / best_strategies / latency / danger files from registry (merge-friendly)."""
    from trading_ai.intelligence.edge_research.artifacts import research_root

    reg = load_registry(runtime_root=runtime_root)
    recs = [r for r in (reg.get("records") or []) if isinstance(r, dict)]
    root = research_root(runtime_root=runtime_root)

    def write_ranked(name: str, filtered: List[Dict[str, Any]], key_hint: str) -> None:
        from trading_ai.intelligence.edge_research.scoring import rank_records

        ranked = rank_records(filtered)
        payload = {
            "artifact": name,
            "key": key_hint,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "ranked": [{"score": s, "record": r} for s, r in ranked[:50]],
        }
        (root / name).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    edges = [r for r in recs if str(r.get("edge_name") or "").strip()]
    strat = [r for r in recs if str(r.get("strategy_name") or "").strip()]
    lat = [r for r in recs if str(r.get("latency_profile_name") or "").strip()]

    write_ranked("best_edges_global.json", edges or recs, "edge_name")
    write_ranked("best_strategies_global.json", strat or recs, "strategy_name")
    write_ranked("best_latency_patterns_global.json", lat or recs, "latency_profile_name")

    dangers = [r for r in recs if "fail" in str(r.get("key_risks") or "").lower() or "trap" in str(r.get("key_risks") or "").lower()]
    (root / "dangerous_failure_modes_global.json").write_text(
        json.dumps(
            {
                "artifact": "dangerous_failure_modes_global",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "rows": dangers[:100],
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    return {"status": "ok", "rankings_updated": True}
