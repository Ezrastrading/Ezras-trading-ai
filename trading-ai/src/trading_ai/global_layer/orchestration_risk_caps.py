"""Daily loss and trading freshness caps — JSON truth under orchestration root."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from trading_ai.global_layer.orchestration_paths import orchestration_risk_caps_path


def _bundled_defaults() -> Path:
    return Path(__file__).resolve().parent / "_governance_data" / "orchestration" / "orchestration_risk_caps.json"


def load_orchestration_risk_caps(*, path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or orchestration_risk_caps_path()
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    fb = _bundled_defaults()
    if fb.is_file():
        return json.loads(fb.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"orchestration_risk_caps_missing:{p}")


def save_orchestration_risk_caps(data: Dict[str, Any], *, path: Optional[Path] = None) -> None:
    p = path or orchestration_risk_caps_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    d = dict(data)
    d.setdefault("truth_version", "orchestration_risk_caps_v1")
    p.write_text(json.dumps(d, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def check_daily_loss_halt(*, path: Optional[Path] = None) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Return (allowed_trading, reason, diagnostics). Blocks when realized loss >= cap.
    """
    caps = load_orchestration_risk_caps(path=path)
    mx = float(caps.get("max_daily_loss_usd_global") or 0.0)
    cur = float(caps.get("current_daily_realized_loss_usd") or 0.0)
    diag = {"max_daily_loss_usd_global": mx, "current_daily_realized_loss_usd": cur}
    if mx <= 0:
        return True, "daily_loss_cap_disabled", diag
    if cur >= mx:
        return False, "daily_loss_cap_breached", diag
    return True, "ok", diag
