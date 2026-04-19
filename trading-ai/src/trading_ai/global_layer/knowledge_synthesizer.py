"""
Merge internal evidence (priority) with ranked external candidates.

Writes knowledge_base, strategy_intelligence, rejected_strategy_ideas.
"""

from __future__ import annotations

from typing import Any, Dict, List

from trading_ai.global_layer.global_memory_store import GlobalMemoryStore
from trading_ai.global_layer.source_quality_ranker import rank_sources


def synthesize(
    *,
    internal: Dict[str, Any],
    external_candidates: List[Dict[str, Any]],
    store: GlobalMemoryStore,
) -> Dict[str, Any]:
    ranked = rank_sources(external_candidates)
    sr = store.load_json("source_rankings.json")
    sr["sources"] = ranked[:100]
    store.save_json("source_rankings.json", sr)

    trades = internal.get("trades") or []
    net_sum = sum(float(t.get("net_pnl_usd") or 0) for t in trades if isinstance(t, dict))
    internal_truth = (
        f"Internal net across logged trades: ${net_sum:.2f}; count={len(trades)}. "
        "Internal evidence outranks external suggestions unless data is thin."
    )

    kb = store.load_json("knowledge_base.json")
    kb.setdefault("global_truths", [])
    if internal_truth not in kb["global_truths"]:
        kb["global_truths"].append(internal_truth)
    kb["global_truths"] = kb["global_truths"][-50:]

    # External → research queue only (not live)
    si = store.load_json("strategy_intelligence.json")
    fams = si.setdefault("strategy_families", [])
    for r in ranked[:8]:
        fams.append(
            {
                "name": r.get("title", "untitled")[:80],
                "avenue_fit": [r.get("avenue_relevance") or "global"],
                "summary": r.get("summary", "")[:400],
                "internal_evidence_strength": "weak" if len(trades) < 10 else "moderate",
                "external_support_strength": "moderate" if r.get("overall_rank", 0) > 0.6 else "weak",
                "post_fee_plausibility": "medium",
                "status": "candidate",
            }
        )
    si["strategy_families"] = fams[-40:]
    store.save_json("strategy_intelligence.json", si)
    store.save_json("knowledge_base.json", kb)

    rej = store.load_json("rejected_strategy_ideas.json")
    rej_list = rej.setdefault("rejected", [])
    seen = {x.get("source_id") for x in rej_list if isinstance(x, dict)}
    for r in ranked:
        if float(r.get("overall_rank") or 0) >= 0.42:
            continue
        sid = r.get("source_id")
        if sid in seen:
            continue
        row = {
            "source_id": sid,
            "title": r.get("title"),
            "reason": "low_signal_external_rank",
            "overall_rank": r.get("overall_rank"),
        }
        rej_list.append(row)
        seen.add(sid)
    rej["rejected"] = rej_list[-200:]
    store.save_json("rejected_strategy_ideas.json", rej)

    return {"knowledge_base": kb, "ranked_sources": ranked[:10]}
