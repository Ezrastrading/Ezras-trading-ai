"""Incident / ticket / learning intelligence layer — 15+ scenarios."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_ai.intelligence.avenue_scope import ensure_intelligence_scope_for_avenue, intelligence_inheritance_manifest
from trading_ai.intelligence.daily_cycle import repeated_low_severity_ticket_ids, run_daily_cycle
from trading_ai.intelligence.governance_bootstrap import ensure_intelligence_control_artifacts, is_action_forbidden, load_governance
from trading_ai.intelligence.learning.registry import load_or_init_registry
from trading_ai.intelligence.learning.updater import ensure_domain_files, maybe_update_domain
from trading_ai.intelligence.paths import (
    learning_domains_dir,
    open_tickets_json_path,
    ticket_routing_log_jsonl_path,
    tickets_jsonl_path,
    what_learned_today_json_path,
)
from trading_ai.intelligence.tickets.ceo_review import build_ceo_session, should_emit_ceo_session, write_ceo_session_files
from trading_ai.intelligence.tickets.classify import classify_signal
from trading_ai.intelligence.tickets.close import close_ticket, materialize_ticket_from_dict, update_ticket_status
from trading_ai.intelligence.tickets.detect import detect_from_execution_event, maybe_create_research_ticket, run_detection_suite
from trading_ai.intelligence.tickets.models import Ticket, TicketSeverity, TicketStatus, TicketType
from trading_ai.intelligence.tickets.route import route_ticket
from trading_ai.intelligence.tickets.store import append_ticket, load_all_tickets, load_ticket_by_id
from trading_ai.multi_avenue.contamination_guard import contamination_assert_paths_distinct_across_avenues


@pytest.fixture
def rt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    return tmp_path


def test_ticket_creation_append_and_index(rt: Path) -> None:
    t = Ticket(
        ticket_type=TicketType.execution_incident,
        severity=TicketSeverity.medium,
        human_plain_english_summary="Test reject",
        trigger_event="rejected order",
    )
    append_ticket(t, runtime_root=rt)
    assert tickets_jsonl_path(runtime_root=rt).exists()
    raw = open_tickets_json_path(runtime_root=rt).read_text(encoding="utf-8")
    assert "open" in raw


def test_ticket_routing_logged(rt: Path) -> None:
    t = Ticket(ticket_type=TicketType.ratio_problem, severity=TicketSeverity.low, ceo_review_required=False)
    rr = route_ticket(t, runtime_root=rt)
    assert "ratios" in rr.domains
    assert ticket_routing_log_jsonl_path(runtime_root=rt).exists()


def test_ceo_session_json_and_txt(rt: Path) -> None:
    t = Ticket(
        ticket_type=TicketType.strategy_degradation,
        severity=TicketSeverity.high,
        human_plain_english_summary="Drawdown spike",
        trigger_event="dd",
        evidence_refs=["ev:1"],
        confidence=0.7,
        learning_update_required=True,
    )
    art = write_ceo_session_files(t, runtime_root=rt)
    assert art.ticket_id == t.ticket_id
    d = rt / "data" / "review" / "ticket_ceo_sessions"
    assert (d / f"{t.ticket_id}.json").exists()
    assert (d / f"{t.ticket_id}.txt").exists()


def test_learning_additive_update_with_evidence(rt: Path) -> None:
    ensure_domain_files("crypto_spot", runtime_root=rt)
    res = maybe_update_domain(
        "crypto_spot",
        reason="ticket-backed note",
        supporting_ticket_ids=["tk_x"],
        confidence=0.7,
        update_type="additive",
        source_scope="avenue/A",
        patch={"what_the_system_currently_knows": "Observed wider spreads during event window (ticket tk_x)."},
        runtime_root=rt,
    )
    assert res.get("ok") is True
    jp = learning_domains_dir(runtime_root=rt) / "crypto_spot.json"
    doc = json.loads(jp.read_text(encoding="utf-8"))
    assert "Observed wider spreads" in doc.get("what_the_system_currently_knows", "")


def test_learning_rejects_without_ticket_refs(rt: Path) -> None:
    ensure_domain_files("stocks", runtime_root=rt)
    res = maybe_update_domain(
        "stocks",
        reason="no tickets",
        supporting_ticket_ids=[],
        confidence=0.9,
        update_type="additive",
        source_scope="gate/B",
        patch={"proven_patterns": ["x"]},
        runtime_root=rt,
    )
    assert res.get("ok") is False


def test_domain_separation_two_files(rt: Path) -> None:
    a = ensure_domain_files("crypto_spot", runtime_root=rt)
    b = ensure_domain_files("stocks", runtime_root=rt)
    assert a["domain"] != b["domain"]


def test_avenue_paths_no_contamination(rt: Path) -> None:
    pa = ensure_intelligence_scope_for_avenue("A", runtime_root=rt)["scoped_tickets_jsonl"]
    pb = ensure_intelligence_scope_for_avenue("B", runtime_root=rt)["scoped_tickets_jsonl"]
    contamination_assert_paths_distinct_across_avenues(pa, pb, avenue_id_a="A", avenue_id_b="B")


def test_daily_cycle_writes_artifacts(rt: Path) -> None:
    append_ticket(
        Ticket(
            ticket_type=TicketType.execution_incident,
            severity=TicketSeverity.medium,
            category="x",
            human_plain_english_summary="e",
        ),
        runtime_root=rt,
    )
    out = run_daily_cycle(runtime_root=rt, thin_confidence_threshold=1.0)
    assert out["ok"] is True
    assert what_learned_today_json_path(runtime_root=rt).exists()


def test_research_ticket_fields(rt: Path) -> None:
    t = maybe_create_research_ticket(
        unknown_topic="New microstructure effect",
        why_it_matters="Fills may be skewed",
        domain_file_to_update="data/learning/domains/market_microstructure.json",
        avenue_id="A",
    )
    assert t.ticket_type == TicketType.market_research_needed
    assert "domain_file_to_update" in (t.extra or {})


def test_repeated_incident_clustering_counts(rt: Path) -> None:
    for i in range(4):
        append_ticket(
            Ticket(
                ticket_type=TicketType.execution_incident,
                severity=TicketSeverity.low,
                category="repeat_cat",
                human_plain_english_summary=f"e{i}",
            ),
            runtime_root=rt,
        )
    dc = run_daily_cycle(runtime_root=rt, thin_confidence_threshold=1.0)
    assert dc["clusters"]["counts"].get("execution_incident", 0) >= 4


def test_governance_bootstrap_and_forbidden(rt: Path) -> None:
    ensure_intelligence_control_artifacts(runtime_root=rt)
    gov = load_governance(runtime_root=rt)
    assert "what_is_forbidden" in gov
    assert (
        is_action_forbidden("attempt autonomous trading outside configured paths", runtime_root=rt) is True
    )


def test_future_avenue_inheritance_manifest() -> None:
    m = intelligence_inheritance_manifest("C", gate_id="g1")
    assert m["execution_not_inherited"] is True
    assert "scoped_ticket_storage" in m["layers"]


def test_registry_init(rt: Path) -> None:
    reg = load_or_init_registry(runtime_root=rt)
    assert "domains" in reg


def test_classify_execution_signals() -> None:
    tt, sev, _cat = classify_signal(trigger="order rejected by venue", source_component="coinbase_engine")
    assert tt == TicketType.execution_incident


def test_close_ticket_status(rt: Path) -> None:
    t = Ticket(human_plain_english_summary="x")
    append_ticket(t, runtime_root=rt)
    ok = close_ticket(t.ticket_id, runtime_root=rt, resolution_note="fixed")
    assert ok is True
    row = load_ticket_by_id(t.ticket_id, runtime_root=rt)
    assert row and row.get("status") == "resolved"


def test_materialize_ticket_roundtrip(rt: Path) -> None:
    t = Ticket(ticket_type=TicketType.pnl_anomaly, human_plain_english_summary="y")
    append_ticket(t, runtime_root=rt)
    row = load_ticket_by_id(t.ticket_id, runtime_root=rt)
    t2 = materialize_ticket_from_dict(row)
    assert t2.ticket_id == t.ticket_id


def test_should_emit_ceo_session_medium() -> None:
    t = Ticket(severity=TicketSeverity.medium, human_plain_english_summary="m")
    assert should_emit_ceo_session(t) is True


def test_repeated_low_escalation_for_ceo() -> None:
    ids = repeated_low_severity_ticket_ids(
        [
            {"severity": "low", "ticket_type": "x", "category": "c", "ticket_id": "a"},
            {"severity": "low", "ticket_type": "x", "category": "c", "ticket_id": "b"},
            {"severity": "low", "ticket_type": "x", "category": "c", "ticket_id": "c"},
        ],
        min_repeat=3,
    )
    assert "a" in ids and "b" in ids


def test_detection_suite_runs(rt: Path) -> None:
    tickets = run_detection_suite(
        execution_events=[{"trigger": "rejected", "reason": "rejected", "source_component": "ex"}],
        strategy_metrics={"win_rate": 0.1, "win_rate_floor": 0.35, "avenue_id": "A"},
        market_snapshot={"spread_pct": 0.05, "spread_warn_pct": 0.01, "venue": "kalshi"},
        system_signals={"ceo_ambiguity_count": 5, "ambiguity_threshold": 3},
        opportunities=[{"repeatable_edge_emerging": True, "confidence": 0.5, "summary": "edge"}],
    )
    assert len(tickets) >= 4


def test_route_adds_ceo_when_flagged(rt: Path) -> None:
    t = Ticket(ticket_type=TicketType.edge_opportunity, severity=TicketSeverity.info, ceo_review_required=True)
    rr = route_ticket(t, runtime_root=rt)
    assert "CEO_review" in rr.domains


def test_build_ceo_session_error_class() -> None:
    t = Ticket(ticket_type=TicketType.runtime_policy_mismatch, human_plain_english_summary="p")
    s = build_ceo_session(t)
    assert s.error_class == "policy"


def test_update_status_investigating(rt: Path) -> None:
    t = Ticket(human_plain_english_summary="z")
    append_ticket(t, runtime_root=rt)
    ok = update_ticket_status(t.ticket_id, TicketStatus.investigating, runtime_root=rt)
    assert ok is True


def test_scoped_mirror_append(rt: Path) -> None:
    t = Ticket(avenue_id="AV1", human_plain_english_summary="scoped")
    append_ticket(t, runtime_root=rt, scoped_mirror=True)
    sp = rt / "data" / "tickets" / "avenues" / "AV1" / "tickets.jsonl"
    assert sp.exists()
