"""Score external sources — prefer official docs, OSS with execution detail, downrank fluff."""

from __future__ import annotations

from typing import Any, Dict, List


def score_source(rec: Dict[str, Any]) -> float:
    st = str(rec.get("source_type") or "").lower()
    base = 0.35
    if st == "official_doc":
        base += 0.35
    elif st in ("repo", "framework"):
        base += 0.25
    elif st == "paper":
        base += 0.2
    elif st == "article":
        base += 0.1
    title = str(rec.get("title") or "").lower()
    if "hummingbot" in title or "execution" in title:
        base += 0.1
    if rec.get("url"):
        base += 0.05
    return min(1.0, max(0.0, base))


def rank_sources(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ranked = []
    for c in candidates:
        s = score_source(c)
        row = dict(c)
        row["overall_rank"] = round(s, 4)
        row["credibility_score"] = round(s * 0.9, 4)
        row["execution_realism_score"] = round(s * 0.85, 4)
        ranked.append(row)
    ranked.sort(key=lambda x: float(x.get("overall_rank") or 0), reverse=True)
    for i, r in enumerate(ranked):
        r["overall_rank_order"] = i + 1
        if "source_name" not in r:
            r["source_name"] = str(r.get("title") or "")[:120]
    return ranked
