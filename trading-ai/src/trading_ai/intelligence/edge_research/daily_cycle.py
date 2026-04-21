"""Daily active research pass — tickets, rankings, CEO session (honest tiers)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.intelligence.edge_research.artifacts import daily_dir, research_root
from trading_ai.intelligence.edge_research.auto_attach import refresh_scoped_rankings_into_gate_files
from trading_ai.intelligence.edge_research.comparisons import run_pairwise_comparisons, update_best_rankings
from trading_ai.intelligence.edge_research.discovery import run_discovery
from trading_ai.multi_avenue.avenue_registry import merged_avenue_definitions
from trading_ai.multi_avenue.gate_registry import merged_gate_rows
from trading_ai.runtime_paths import ezras_runtime_root


def _review_dir(runtime_root: Path) -> Path:
    p = runtime_root / "data" / "review"
    p.mkdir(parents=True, exist_ok=True)
    return p


def run_daily_edge_research_cycle(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    disc = run_discovery(runtime_root=root)
    cmpo = run_pairwise_comparisons(runtime_root=root, max_pairs=30)
    ranks = update_best_rankings(runtime_root=root)

    for g in merged_gate_rows(runtime_root=root):
        try:
            refresh_scoped_rankings_into_gate_files(str(g["avenue_id"]), str(g["gate_id"]), runtime_root=root)
        except Exception:
            pass

    best_disc: List[str] = []
    worst_fail: List[str] = []
    stop_doing: List[str] = []
    test_next: List[str] = []
    promising: Dict[str, List[str]] = {}

    reg_path = research_root(runtime_root=root) / "research_registry.json"
    if reg_path.is_file():
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
        for r in (reg.get("records") or [])[:200]:
            if not isinstance(r, dict):
                continue
            summ = str(r.get("operator_plain_english_summary") or "")[:200]
            st = str(r.get("current_status") or "")
            if st in ("live_supported", "staged_supported") and summ:
                best_disc.append(summ)
            if "fail" in str(r.get("key_risks") or "").lower() and summ:
                worst_fail.append(summ)
            if st == "rejected" and summ:
                stop_doing.append(summ)
            nxt = str(r.get("recommended_next_test") or "").strip()
            if nxt:
                test_next.append(nxt)
            aid = str(r.get("avenue_id") or "_unscoped")
            promising.setdefault(aid, []).append(summ[:120] if summ else r.get("record_id"))

    session = {
        "artifact": "daily_edge_research_review",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "discovery": disc,
        "comparisons": cmpo,
        "rankings": ranks,
        "ceo_lens": {
            "best_discoveries": best_disc[:20],
            "worst_failures": worst_fail[:20],
            "what_to_stop_doing": stop_doing[:20],
            "what_to_test_next": list(dict.fromkeys(test_next))[:30],
            "promising_by_avenue": {k: v[:15] for k, v in promising.items()},
        },
        "honesty": "Mock/stage/live labels come from record status and proving artifacts — never treated as live PnL edge by default.",
    }

    dd = daily_dir(runtime_root=root)
    dd.mkdir(parents=True, exist_ok=True)
    (dd / "daily_edge_research_review.json").write_text(json.dumps(session, indent=2, default=str), encoding="utf-8")
    txt_lines = [
        f"Daily edge research — {session['generated_at']}",
        "",
        "Best discoveries (summaries):",
        *[f"- {x}" for x in best_disc[:10]],
        "",
        "Worst failures / traps:",
        *[f"- {x}" for x in worst_fail[:10]],
        "",
        "What to test next:",
        *[f"- {x}" for x in test_next[:10]],
        "",
    ]
    (dd / "daily_edge_research_review.txt").write_text("\n".join(txt_lines), encoding="utf-8")

    ceo = {
        "artifact": "daily_edge_ceo_session",
        "generated_at": session["generated_at"],
        "avenues_considered": [str(a.get("avenue_id")) for a in merged_avenue_definitions(runtime_root=root)],
        **session["ceo_lens"],
    }
    rv = _review_dir(root)
    (rv / "daily_edge_ceo_session.json").write_text(json.dumps(ceo, indent=2, default=str), encoding="utf-8")
    (rv / "daily_edge_ceo_session.txt").write_text(
        json.dumps(ceo, indent=2, default=str) + "\n",
        encoding="utf-8",
    )

    return {"status": "ok", "session": session}
