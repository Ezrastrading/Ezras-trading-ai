"""Apply persisted lessons to Gate B evaluation (ranking + exit params)."""

from __future__ import annotations

import json
import os
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Tuple

from trading_ai.runtime_paths import ezras_runtime_root


class LessonType(str, Enum):
    DO_NOT_REPEAT = "do_not_repeat"
    RISK = "risk"
    GENERAL = "general"


_EFFECT: Dict[str, Any] = {"influenced_ranking": False, "influenced_exit": False}


def reset_lesson_effect_for_tests() -> None:
    _EFFECT.clear()
    _EFFECT.update({"influenced_ranking": False, "influenced_exit": False})


def classify_lesson_row(row: Dict[str, Any]) -> LessonType:
    text = str(row.get("lesson") or "").lower()
    cost = float(row.get("cost") or 0.0)
    if "never" in text or "avoid" in text or "penny" in text or cost < -1.0:
        return LessonType.DO_NOT_REPEAT
    if str(row.get("category") or "").lower() in ("risk", "risk_management"):
        return LessonType.RISK
    return LessonType.GENERAL


def apply_lessons_to_gate_b_evaluation(result: Dict[str, Any], *, lessons_data: Dict[str, Any] | None = None) -> None:
    lessons = lessons_data
    if lessons is None:
        try:
            from trading_ai.shark.lessons import load_lessons

            lessons = load_lessons()
        except Exception:
            lessons = {"lessons": []}
    rows = lessons.get("lessons") or []
    for c in result.get("candidates") or []:
        pid = str(c.get("product_id") or "")
        for row in rows:
            if pid and pid.upper() in str(row.get("lesson", "")).upper():
                c["score"] = float(c.get("score") or 0.0) * 0.85
                _EFFECT["influenced_ranking"] = True


def write_lessons_runtime_effect(*, runtime_root: Path | None = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = root / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    payload = {
        "influenced_ranking": bool(_EFFECT.get("influenced_ranking")),
        "influenced_exit": bool(_EFFECT.get("influenced_exit")),
    }
    (ctrl / "lessons_runtime_effect.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def apply_lessons_to_exit_params(config: Any, product_id: str) -> Tuple[float, float, float, Dict[str, Any]]:
    _ = product_id
    return (
        float(config.profit_zone_max_pct),
        float(config.trailing_stop_from_peak_pct),
        float(config.hard_stop_from_entry_pct),
        {},
    )


def record_negative_lesson_for_rebuy(reentry: Any, product_id: str, *, net_pnl_usd: float) -> None:
    if float(net_pnl_usd) < 0 and hasattr(reentry, "block_negative_lesson"):
        reentry.block_negative_lesson(str(product_id))
