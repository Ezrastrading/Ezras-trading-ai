"""Autonomous blocker playbook resolution."""

from trading_ai.orchestration.autonomous_blocker_playbook import (
    enrich_active_blockers_with_playbook,
    resolve_playbook_entries_for_blocker,
)


def test_consecutive_cycles_regex():
    rows = resolve_playbook_entries_for_blocker(
        "insufficient_consecutive_autonomous_live_ok_cycles_need_5_have_2"
    )
    assert rows[0]["playbook_match"] is True
    assert rows[0]["playbook_id"] == "insufficient_consecutive_autonomous_cycles"
    assert rows[0]["parsed_groups"]["need"] == "5"
    assert rows[0]["parsed_groups"]["have"] == "2"


def test_failure_stop_substring():
    rows = resolve_playbook_entries_for_blocker("failure_stop_not_runtime_verified")
    assert rows[0]["playbook_id"] == "failure_stop_runtime"


def test_unmapped_still_returns_row():
    rows = resolve_playbook_entries_for_blocker("totally_unknown_custom_blocker_xyz")
    assert rows[0]["playbook_match"] is False
    assert rows[0]["playbook_id"] == "unmapped_blocker"


def test_enrich_dedupes():
    out = enrich_active_blockers_with_playbook(
        ["failure_stop_not_runtime_verified", "failure_stop_not_runtime_verified", "lock_exclusivity_not_runtime_verified"]
    )
    assert len(out) == 2
