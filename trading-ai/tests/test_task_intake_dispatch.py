from __future__ import annotations

import os
import tempfile
from pathlib import Path


def test_task_intake_writes_inboxes_and_state() -> None:
    with tempfile.TemporaryDirectory(prefix="ezra_intake_test_") as td:
        runtime_root = Path(td).resolve()
        os.environ["EZRAS_RUNTIME_ROOT"] = str(runtime_root)

        # Route a couple of tasks (append-only).
        from trading_ai.global_layer.task_router import route_task_shadow
        from trading_ai.global_layer.bot_types import BotRole

        t1 = route_task_shadow(
            avenue="A",
            gate="none",
            task_type="mission_goals::validation",
            source_bot_id="test",
            role=BotRole.RISK.value,
            evidence_ref="unit_test",
        )
        t1["priority"] = 100
        t2 = route_task_shadow(
            avenue="A",
            gate="none",
            task_type="comparisons::avenue",
            source_bot_id="test",
            role=BotRole.LEARNING.value,
            evidence_ref="unit_test",
        )
        t2["priority"] = 50

        from trading_ai.global_layer.task_intake import run_task_intake_once

        rep = run_task_intake_once(runtime_root=runtime_root)
        assert rep.get("ok") is True

        state_p = runtime_root / "data" / "control" / "task_intake_state.json"
        rollup_p = runtime_root / "data" / "control" / "task_rollup.json"
        assert state_p.is_file()
        assert rollup_p.is_file()

        inbox_dir = runtime_root / "data" / "control" / "bot_inboxes"
        assert inbox_dir.is_dir()
        # There should be at least one inbox json written (could be an "unassigned_*" bucket).
        assert any(p.suffix == ".json" for p in inbox_dir.iterdir())

