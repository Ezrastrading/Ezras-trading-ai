"""Intelligence integration from submit_order outcomes (non-live)."""

from __future__ import annotations

from pathlib import Path

import pytest

from trading_ai.intelligence.integration.live_hooks import record_shark_submit_outcome
from trading_ai.shark.models import ExecutionIntent, OrderResult


def test_record_shark_submit_blocked_does_not_claim_venue_truth(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    intent = ExecutionIntent(
        market_id="KXTEST-24",
        outlet="kalshi",
        side="yes",
        stake_fraction_of_capital=0.01,
        edge_after_fees=0.01,
        estimated_win_probability=0.5,
        hunt_types=[],
        source="test",
        shares=1,
        expected_price=0.5,
        meta={"strategy_key": "unit"},
    )
    res = OrderResult(
        order_id="",
        filled_price=0.0,
        filled_size=0.0,
        timestamp=0.0,
        status="system_execution_lock",
        outlet="kalshi",
        raw={},
        success=False,
        reason="lock",
    )
    out = record_shark_submit_outcome(intent, res)
    assert out.get("venue_reached") is False


def test_ceo_session_defaults_templated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.intelligence.tickets.ceo_review import build_ceo_session
    from trading_ai.intelligence.tickets.models import Ticket, TicketSeverity, TicketType

    t = Ticket(
        ticket_type=TicketType.execution_incident,
        severity=TicketSeverity.low,
        trigger_event="test",
        human_plain_english_summary="hello",
        machine_summary="{}",
    )
    s = build_ceo_session(t)
    assert s.ceo_statement_mode == "templated_summary"
    assert s.venue_truth_verified is False
    assert s.evidence_chain_present is False
