# Autonomous gap closer

**Purpose:** Single honest merge of autonomous blockers, classifications (structural vs historical), and exact next runtime steps from `build_autonomous_operator_path`.

**Does not:** Substitute code changes for venue proof or arm venue orders.

**Artifacts:** `autonomous_gap_closer.json`, `autonomous_next_steps.json`, `autonomous_progress_delta.json` (vs previous `autonomous_gap_closer.previous.json`).

**Command:** `python -m trading_ai.deployment autonomous-gap-closer`

**Honesty:** “Closer than yesterday” only compares blocker counts/hashes from artifacts — not optimism or PnL.
