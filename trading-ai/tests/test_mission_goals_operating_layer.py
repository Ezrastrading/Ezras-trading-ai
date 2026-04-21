from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from trading_ai.global_layer.mission_goals_operating_layer import classify_mission_pace, build_daily_operating_plan
from trading_ai.shark.mission import evaluate_trade_against_mission
from trading_ai.shark.lessons import classify_lessons_smoke_status, load_lessons


def test_mission_pace_classification() -> None:
    behind = classify_mission_pace(required_daily_pct=5.0, actual_daily_pct=3.0)
    assert behind.pace_state == "behind_pace"
    on = classify_mission_pace(required_daily_pct=5.0, actual_daily_pct=5.1)
    assert on.pace_state == "on_pace"
    ahead = classify_mission_pace(required_daily_pct=5.0, actual_daily_pct=6.0)
    assert ahead.pace_state == "ahead_of_pace"


def test_probability_tiers_block_and_allowance() -> None:
    # <63 blocked
    r0 = evaluate_trade_against_mission("kalshi", "KXBTC", 1.0, 0.62, 200.0)
    assert r0["approved"] is False
    assert r0["probability_tier"] == 0

    # 63–76 tier 1 (protective sizing)
    r1 = evaluate_trade_against_mission("kalshi", "KXBTC", 1.0, 0.63, 200.0)
    assert r1["approved"] is True
    assert r1["probability_tier"] == 1

    # 77–90 tier 2
    r2 = evaluate_trade_against_mission("kalshi", "KXBTC", 1.0, 0.80, 200.0)
    assert r2["approved"] is True
    assert r2["probability_tier"] == 2

    # 90+ tier 3 (strongest allowance within caps)
    r3 = evaluate_trade_against_mission("kalshi", "KXBTC", 1.0, 0.90, 200.0)
    assert r3["approved"] is True
    assert r3["probability_tier"] == 3


def test_mission_goals_influence_prioritization() -> None:
    pace = classify_mission_pace(required_daily_pct=5.0, actual_daily_pct=3.0)
    plan = build_daily_operating_plan(pace=pace, active_goal_id="GOAL_A")
    impl = plan["daily_loop"]["implementation"]
    # When behind pace, we bias toward safe throughput improvements (not reckless sizing).
    assert any("safe trade throughput" in x for x in impl)


def test_lessons_fresh_server_is_warn_not_fail() -> None:
    # Defaults are allowed to be incomplete; only broken/corrupt should FAIL.
    lessons = load_lessons()
    out = classify_lessons_smoke_status(lessons)
    assert out["healthy_structure"] is True
    assert out["status"] in ("WARN", "PASS")


@pytest.mark.skipif(sys.platform.startswith("win"), reason="script path assumptions are unix-like")
def test_master_smoke_output_reflects_new_tiers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path(__file__).resolve().parents[1]
    script = repo / "scripts" / "master_smoke_test.py"
    env = dict(os.environ)
    # Ensure we don't require runtime secrets for this smoke run.
    env.pop("SUPABASE_URL", None)
    env.pop("SUPABASE_KEY", None)
    env.pop("SUPABASE_SERVICE_ROLE_KEY", None)
    # Keep output deterministic-ish by avoiding any host runtime root assumptions.
    env["EZRAS_RUNTIME_ROOT"] = str(tmp_path)
    out = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, env=env, timeout=60)
    assert out.returncode == 0
    s = out.stdout
    assert "Tier BLOCK (<63)" in s
    assert "Tier 1 (63–76) protective sizing" in s
    assert "Operating layer pace_state" in s

