"""Daily learning / research cycle — scans tickets, clusters, writes review artifacts."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.intelligence.learning.domain_catalog import DOMAIN_IDS
from trading_ai.intelligence.learning.registry import load_or_init_registry
from trading_ai.intelligence.learning.synthesis import synthesize_learning_priorities
from trading_ai.intelligence.learning.updater import ensure_domain_files
from trading_ai.intelligence.paths import (
    what_learned_today_json_path,
    what_not_to_do_tomorrow_json_path,
    what_to_test_tomorrow_json_path,
)
from trading_ai.intelligence.tickets.detect import maybe_create_research_ticket
from trading_ai.intelligence.tickets.ceo_review import write_daily_learning_session_review
from trading_ai.intelligence.tickets.models import Ticket
from trading_ai.intelligence.tickets.store import append_ticket, iter_tickets_for_clustering, load_all_tickets
from trading_ai.intelligence.tickets.route import route_ticket


def _cluster_recurring(tickets: List[Dict[str, Any]], *, min_count: int = 3) -> Dict[str, Any]:
    cats = Counter(str(t.get("ticket_type") or t.get("category") or "unknown") for t in tickets)
    return {"recurring_categories": {k: v for k, v in cats.items() if v >= min_count}, "counts": dict(cats)}


def run_daily_cycle(
    *,
    runtime_root: Optional[Path] = None,
    as_of: Optional[date] = None,
    thin_confidence_threshold: float = 0.25,
    max_research_tickets_per_cycle: int = 5,
) -> Dict[str, Any]:
    """
    1. Scan tickets
    2. Cluster recurring problems/opportunities
    3. Update learning priorities (advisory)
    4. Optionally enqueue research tickets for thin domains
    5. CEO learning session + three daily learning files
    """
    as_of = as_of or datetime.now(timezone.utc).date()
    load_or_init_registry(runtime_root=runtime_root)

    recent = list(iter_tickets_for_clustering(runtime_root=runtime_root, since_days=14.0))
    clusters = _cluster_recurring(recent)

    domain_docs: List[Dict[str, Any]] = []
    for did in DOMAIN_IDS:
        domain_docs.append(ensure_domain_files(did, runtime_root=runtime_root))

    priorities = synthesize_learning_priorities(domain_docs)
    research_tickets: List[Ticket] = []
    for did in priorities.get("priority_domains") or []:
        if len(research_tickets) >= max(0, max_research_tickets_per_cycle):
            break
        doc = next((d for d in domain_docs if d.get("domain") == did), None)
        if doc and float(doc.get("confidence") or 0) <= thin_confidence_threshold:
            research_tickets.append(
                maybe_create_research_ticket(
                    unknown_topic=f"Thin knowledge for domain `{did}`",
                    why_it_matters="Repeated incidents may map here; understanding is underdeveloped.",
                    domain_file_to_update=f"data/learning/domains/{did}.json",
                )
            )

    for rt in research_tickets:
        rr = route_ticket(rt, runtime_root=runtime_root)
        rt.routed_domains = rr.domains
        append_ticket(rt, runtime_root=runtime_root)

    learned_lines = [
        f"Cluster summary: {json.dumps(clusters['recurring_categories'], ensure_ascii=False)}",
        f"Priority domains: {priorities.get('priority_domains', [])}",
    ]
    learned = {
        "date": as_of.isoformat(),
        "utc_generated_at": datetime.now(timezone.utc).isoformat(),
        "clusters": clusters,
        "learning_priorities": priorities,
        "what_the_system_learned_today": learned_lines,
        "honesty": "Narrative is descriptive of internal artifacts only — not external market mastery.",
    }

    wlearn = what_learned_today_json_path(runtime_root=runtime_root)
    wlearn.parent.mkdir(parents=True, exist_ok=True)
    wlearn.write_text(json.dumps(learned, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    not_do = {
        "date": as_of.isoformat(),
        "what_not_to_do_tomorrow": [
            "Do not expand execution authority based on this summary alone.",
            "Do not treat clustered heuristics as proven edge without ticket-backed evidence.",
            "Do not bypass Gate A / Gate B safety reviews when reacting to incidents.",
        ],
    }
    what_not_to_do_tomorrow_json_path(runtime_root=runtime_root).write_text(
        json.dumps(not_do, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    tests = {
        "date": as_of.isoformat(),
        "what_to_test_tomorrow": [
            "Reconcile open tickets against venue logs for the same window.",
            "Validate stale-quote detection against measured websocket latency.",
            "Review thin domains with operators before large capital moves.",
        ],
    }
    what_to_test_tomorrow_json_path(runtime_root=runtime_root).write_text(
        json.dumps(tests, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    session_body = {
        "date": as_of.isoformat(),
        "summary_text": "\n".join(learned_lines),
        "open_ticket_count": len([t for t in load_all_tickets(runtime_root=runtime_root) if t.get("status") == "open"]),
        "ceo_review": "Supervised review required before any policy or execution change.",
    }
    write_daily_learning_session_review(session_body, runtime_root=runtime_root)

    return {
        "ok": True,
        "date": as_of.isoformat(),
        "clusters": clusters,
        "research_tickets_created": len(research_tickets),
        "priorities": priorities,
    }


def repeated_low_severity_ticket_ids(
    tickets: List[Dict[str, Any]],
    *,
    min_repeat: int = 3,
) -> List[str]:
    """Identify low-severity tickets that repeat per category for CEO escalation."""
    buckets: Dict[str, List[str]] = {}
    for t in tickets:
        if str(t.get("severity")) != "low":
            continue
        key = f"{t.get('ticket_type')}|{t.get('category')}"
        buckets.setdefault(key, []).append(str(t.get("ticket_id")))
    out: List[str] = []
    for _k, ids in buckets.items():
        if len(ids) >= min_repeat:
            out.extend(ids)
    return out
