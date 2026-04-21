## Micro-trade readiness checklist (non-live)

This checklist is **strict**. Only mark PASS when there is a mechanism and a machine-readable artifact.

### Required machine-readable outputs

- `/opt/ezra-runtime/data/control/deploy_preflight.json`
- `/opt/ezra-runtime/data/control/deployed_environment_smoke.json`
- `/opt/ezra-runtime/data/control/micro_trade_readiness.json`
- `/opt/ezra-runtime/data/control/final_switch_readiness.json`

### Gate: live must remain disabled

- **PASS** when:
  - `deploy_preflight.json` → `checks.live_disabled.ok == true`
  - `deployed_environment_smoke.json` → `live_disabled.ok == true`
  - `micro_trade_readiness.json` → `live_disabled.ok == true`
  - `final_switch_readiness.json` → `live_disabled.ok == true`

### Gate: services + supervisors are healthy

- **PASS** when:
  - `deployed_environment_smoke.json` → `ops_supervisor.ok == true` and `research_supervisor.ok == true`
  - `deployed_environment_smoke.json` → `expected_artifacts_exist` all true
  - `final_switch_readiness.json` → `freshness.ops_loop_status_fresh.ok == true` and `freshness.research_loop_status_fresh.ok == true`

### Gate: task routing + consumption is real

- **PASS** when:
  - `deployed_environment_smoke.json` → `task_probe.ok == true`
  - `deployed_environment_smoke.json` → `bot_inboxes.ok == true`
  - `micro_trade_readiness.json` → `bot_inboxes.ok == true`

### Gate: missing runtime paths / missing imports / missing env files

- **PASS** when:
  - `deploy_preflight.json` → `checks.filesystem.*.ok == true`
  - `deploy_preflight.json` → `checks.systemd_unit_templates.*.ok == true`
  - `deploy_preflight.json` → `checks.critical_imports.ok == true`
  - `deployed_environment_smoke.json` → `imports_ok == true`

### Micro-trade readiness verdict

- **MICRO-TRADE READY** when:
  - `micro_trade_readiness.json` → `ok == true`
  - and live is still disabled everywhere.

### What remains intentionally manual

- **Intentional human action only**:
  - confirm micro-trade parameters
  - flip live enablement (outside this pass)

