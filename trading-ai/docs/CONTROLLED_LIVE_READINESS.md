# Controlled live readiness

Canonical commands:

- `python -m trading_ai.deployment controlled-live-readiness`
- `python -m trading_ai.deployment final-live-readiness` (alias)

Artifacts:

- `data/control/controlled_live_readiness.json` — structured categories (supervised vs autonomous vs Gate A vs Gate B vs shared infra).
- `data/control/controlled_live_readiness_summary.txt` — concise human summary.

Historical autonomous notes remain under `avenue_a_autonomous.historical_notes_separate` and are never promoted to active blockers in the summary.
