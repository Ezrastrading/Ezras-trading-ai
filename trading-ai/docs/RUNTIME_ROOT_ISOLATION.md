# Runtime root isolation

`trading_ai.core.system_guard.get_system_guard` is keyed by the resolved `EZRAS_RUNTIME_ROOT` so halt/state files under `<root>/shark/state/` do not bleed across processes or tests using different roots.

Helpers:

- `reset_system_guard_singletons_for_tests()` clears in-process caches (tests only).
