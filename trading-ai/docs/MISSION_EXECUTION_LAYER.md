# Mission execution layer

**Purpose:** Evidence-based mission state (stage, goals, blocked actions, today/tomorrow plans) under `data/control/organism/`.

**Does not:** Grant live authority, promise returns, or override gates.

**Artifacts:** `mission_execution_state.json`, `avenue_goal_state.json`, `gate_goal_state.json`, `mission_progress_timeline.jsonl`, `today_best_actions.json`, `tomorrow_best_actions.json`.

**Command:** `python -m trading_ai.deployment mission-execution-status`

**Operator rule:** If `controlled_live_readiness.json` disagrees with intuition, trust the artifacts.
