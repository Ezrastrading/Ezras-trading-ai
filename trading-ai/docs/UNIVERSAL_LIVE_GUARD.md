# Universal live guard

`trading_ai.safety.universal_live_guard` holds a registry of declared avenue/gate wiring classes (`fully_wired`, `partially_wired`, `legacy_guarded_only`).

- Artifact: `data/control/universal_live_guard_truth.json` via `write_universal_live_guard_truth`.
- **Fail-closed evaluation:** `evaluate_universal_live_guard(avenue, gate)` denies unknown keys when `fail_closed=True` (for explicit contract checks). This does not remove venue-specific failsafe code paths.
