"""Bot heartbeat and stale detection — registry fields updated; cleanup artifacts."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.global_layer.bot_registry import get_bot, load_registry, patch_bot, save_registry
from trading_ai.global_layer.orchestration_paths import orchestration_health_path
from trading_ai.global_layer.orchestration_schema import OrchestrationBotStatus

import json


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def touch_heartbeat(bot_id: str, *, path: Optional[Path] = None) -> Dict[str, Any]:
    return patch_bot(bot_id, {"last_heartbeat_at": _iso()}, path=path)


def record_task_complete(bot_id: str, *, path: Optional[Path] = None) -> Dict[str, Any]:
    b = get_bot(bot_id, path=path) or {}
    perf = dict(b.get("performance") or {})
    perf["tasks_completed"] = int(perf.get("tasks_completed") or 0) + 1
    perf["last_task_completed_at"] = _iso()
    return patch_bot(bot_id, {"performance": perf}, path=path)


def run_stale_sweep(
    *,
    stale_after_sec: int = 3600,
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Mark bots stale/degraded if heartbeat missing beyond threshold."""
    reg = load_registry(path)
    now = datetime.now(timezone.utc)
    changed: List[str] = []
    bots = []
    for b in reg.get("bots") or []:
        hb = str(b.get("last_heartbeat_at") or "").strip()
        nb = dict(b)
        if hb:
            try:
                hbt = datetime.fromisoformat(hb.replace("Z", "+00:00"))
                if (now - hbt.replace(tzinfo=timezone.utc)) > timedelta(seconds=stale_after_sec):
                    if str(nb.get("status")) != OrchestrationBotStatus.DISABLED.value:
                        nb["status"] = OrchestrationBotStatus.STALE.value
                        nb["demotion_risk"] = True
                        changed.append(str(nb.get("bot_id")))
            except ValueError:
                nb["status"] = OrchestrationBotStatus.DEGRADED.value
                changed.append(str(nb.get("bot_id")))
        else:
            nb["status"] = OrchestrationBotStatus.STALE.value
            nb["demotion_risk"] = True
            changed.append(str(nb.get("bot_id")))
        bots.append(nb)
    reg["bots"] = bots
    save_registry(reg, path=path)
    health = {
        "truth_version": "orchestration_stale_sweep_v1",
        "stale_or_degraded": changed,
        "generated_at": _iso(),
    }
    orchestration_health_path().parent.mkdir(parents=True, exist_ok=True)
    orchestration_health_path().write_text(json.dumps(health, indent=2) + "\n", encoding="utf-8")
    return health
