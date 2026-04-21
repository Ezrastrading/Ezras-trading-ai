"""Lesson runtime influence on Gate B (ranking / rebuy controller)."""

from __future__ import annotations

from pathlib import Path

import pytest

from trading_ai.shark.coinbase_spot.gate_b_reentry import ReentryController
from trading_ai.shark.lesson_runtime_influence import (
    LessonType,
    apply_lessons_to_gate_b_evaluation,
    classify_lesson_row,
    record_negative_lesson_for_rebuy,
    reset_lesson_effect_for_tests,
    write_lessons_runtime_effect,
)


def test_classify_lesson_types() -> None:
    assert classify_lesson_row({"lesson": "Never buy penny coins", "cost": -5.0, "category": "x"}) == LessonType.DO_NOT_REPEAT


def test_ranking_penalty_applies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    reset_lesson_effect_for_tests()
    result = {
        "candidates": [{"product_id": "BTC-USD", "score": 0.9, "momentum_score": 0.8}],
        "rejected": [],
    }
    lessons = {
        "lessons": [
            {
                "lesson": "Avoid BTC-USD when spread widens",
                "cost": -1.0,
                "category": "risk",
                "date": "2026-01-01",
                "session": "t",
                "platform": "coinbase",
            }
        ],
        "do_not_repeat": [],
    }
    apply_lessons_to_gate_b_evaluation(result, lessons_data=lessons)
    assert result["candidates"][0]["score"] < 0.9
    eff = write_lessons_runtime_effect(runtime_root=tmp_path)
    assert eff["influenced_ranking"] is True
    p = tmp_path / "data" / "control" / "lessons_runtime_effect.json"
    assert p.is_file()


def test_negative_lesson_rebuy_block() -> None:
    reset_lesson_effect_for_tests()
    r = ReentryController()
    record_negative_lesson_for_rebuy(r, "ETH-USD", net_pnl_usd=-10.0)
    ok, reasons = r.can_reenter("ETH-USD", momentum_score=0.99, new_breakout_confirmed=True)
    assert ok is False
    assert any("negative_lesson" in x for x in reasons)
