"""Global lesson promotion requires lesson_generalizer approval."""

from __future__ import annotations

from trading_ai.nte.global_scope.lesson_generalizer import approve_global_promotion


def test_single_avenue_evidence_not_promoted():
    lesson = {"text": "tight spreads matter"}
    evidence = [{"avenue": "coinbase", "n": 5}]
    assert approve_global_promotion(lesson=lesson, evidence=evidence, min_avenues=2) is False


def test_cross_avenue_evidence_promoted():
    lesson = {"text": "risk caps"}
    evidence = [{"avenue": "coinbase"}, {"avenue": "kalshi"}]
    assert approve_global_promotion(lesson=lesson, evidence=evidence, min_avenues=2) is True
