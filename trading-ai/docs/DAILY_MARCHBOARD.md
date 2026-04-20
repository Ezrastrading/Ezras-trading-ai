# Daily / weekly marchboard

**Purpose:** One-page rollup for CEO sessions and operators: goal, blockers, opportunities, experiments, waste signals, plans.

**Artifacts:** `daily_marchboard.json`, `weekly_marchboard.json` under `data/control/organism/`.

**Commands:**
- `python -m trading_ai.deployment daily-marchboard`
- `python -m trading_ai.deployment weekly-marchboard`
- `python -m trading_ai.deployment organism-coordination-bundle` (writes all organism artifacts)

**Note:** Rankings are attention hints from current artifacts — not forecasts or guaranteed outcomes.
