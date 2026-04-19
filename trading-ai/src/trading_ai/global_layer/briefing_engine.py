"""2–3× daily low-token briefings — internal reality first, external enrichment second."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from trading_ai.global_layer.global_memory_store import GlobalMemoryStore
from trading_ai.global_layer.internal_data_reader import read_normalized_internal
from trading_ai.nte.memory.store import MemoryStore

logger = logging.getLogger(__name__)


def _iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


class BriefingEngine:
    def __init__(self, store: Optional[GlobalMemoryStore] = None) -> None:
        self.store = store or GlobalMemoryStore()

    def run_once(self, *, touch_research_memory: bool = True) -> Dict[str, Any]:
        internal = read_normalized_internal()
        spd = self.store.load_json("speed_progression.json")
        si = self.store.load_json("strategy_intelligence.json")
        fams = si.get("strategy_families") or []
        top_ext = fams[-3:] if fams else []

        led = internal["capital_ledger"]
        actions: List[str] = (spd.get("best_path") or {}).get("top_3_actions") or []
        blockers = spd.get("blockers") or []

        top_actions = actions[:3] if len(actions) >= 3 else actions + ["Review capital ledger vs trades"] * (3 - len(actions))
        top_actions = top_actions[:3]

        risks = [
            b.get("name", "unknown") for b in blockers[:3]
        ]
        if len(risks) < 3:
            risks.extend(["fee_drag", "venue_outage", "model_drift"][: 3 - len(risks)])

        research_pri = [
            "Validate strongest internal edge before new externals",
            "Sandbox any new strategy family",
            "Cross-check Supabase vs local trade log",
        ]

        lines = [
            f"### {_iso()} | goal **{spd.get('active_goal', '?')}**",
            "",
            "**A — Internal**",
            f"- Equity (ledger): ${float(led.get('net_equity_estimate_usd') or 0):.2f}; deposits ${float(led.get('deposits_usd') or 0):.2f}; realized ${float(led.get('realized_pnl_usd') or 0):.2f}",
            f"- Best avenue: {spd.get('strongest_avenue')}; weakest: {spd.get('weakest_avenue')}",
            f"- Bottleneck: {blockers[0]['name'] if blockers else 'none'}",
            "",
            "**B — External (research queue only)**",
        ]
        if top_ext:
            for f in top_ext:
                lines.append(f"- {f.get('name', '?')[:60]} — {f.get('status', 'candidate')}")
        else:
            lines.append("- (no new ranked externals)")

        lines.extend(
            [
                "",
                "**Top 3 actions**",
                *[f"{i+1}. {a}" for i, a in enumerate(top_actions)],
                "",
                "**Top 3 risks**",
                *[f"{i+1}. {r}" for i, r in enumerate(risks[:3])],
                "",
                "**Top 3 research priorities**",
                *[f"{i+1}. {r}" for i, r in enumerate(research_pri)],
            ]
        )
        try:
            from trading_ai.nte.ceo.followup import prepare_ceo_followup_briefing

            fu = prepare_ceo_followup_briefing(
                session_id=f"briefing_{int(time.time())}",
            )
            lines.extend(["", fu["markdown"]])
        except Exception as exc:
            logger.debug("ceo followup briefing: %s", exc)

        body = "\n".join(lines)
        self.store.append_md("briefing_log.md", body)

        if touch_research_memory:
            try:
                nte = MemoryStore()
                nte.ensure_defaults()
                rm = nte.load_json("research_memory.json")
                tail = rm.setdefault("briefing_pointers", [])
                tail.append(
                    {
                        "at": _iso(),
                        "goal": spd.get("active_goal"),
                        "research_priorities": research_pri,
                    }
                )
                rm["briefing_pointers"] = tail[-30:]
                nte.save_json("research_memory.json", rm)
            except Exception as exc:
                logger.debug("briefing research_memory: %s", exc)

        return {
            "text": body,
            "active_goal": spd.get("active_goal"),
            "top_3_actions": top_actions,
            "top_3_risks": risks[:3],
            "top_3_research_priorities": research_pri,
        }
