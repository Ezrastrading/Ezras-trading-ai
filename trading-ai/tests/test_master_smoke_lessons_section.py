import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from master_smoke_test import _lessons_section_status


def test_lessons_smoke_passes_when_structure_is_healthy_even_if_day_a_incomplete() -> None:
    lessons = {
        "lessons": [{"platform": "coinbase", "lesson": "x"}],
        "rules": ["r1"],
        "do_not_repeat": ["dnr1"],
        "day_a_complete": False,
    }
    status, healthy, day_a_complete = _lessons_section_status(lessons)
    assert healthy is True
    # Healthy-but-incomplete should not hard FAIL (bootstrap-friendly).
    assert status == "⚠️ WARN"
    assert day_a_complete is False


def test_lessons_smoke_fails_when_structure_missing() -> None:
    status, healthy, _ = _lessons_section_status({})
    assert healthy is False
    assert status == "❌ FAIL"

