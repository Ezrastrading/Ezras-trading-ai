## Final switch readiness audit (deployment layer)

Truth version: `final_switch_readiness_audit_v1`

This document is **strict**. Items are only marked PASS when there is:
- a **mechanism** in-repo, and
- a **server command** that produces a **machine-readable proof artifact**.

### Results (PASS / PARTIAL / FAIL)

- **public/private overlay correctness**: **PARTIAL**
  - **Why**: `deployed_environment_smoke.py` checks sys.path ordering and expects `PYTHONPATH` overlay, but this must be proven on the target server by running the smoke under the systemd environment.
  - **Proof artifact**: `/opt/ezra-runtime/data/control/deployed_environment_smoke.json` (`python_overlay`)

- **service persistence on boot**: **PARTIAL**
  - **Why**: unit templates and installer exist; PASS requires running the reboot proof steps in `DEPLOYMENT_BOOT_PROOF.md`.
  - **Proof artifact**: `systemctl is-enabled` + post-reboot fresh timestamps in loop status artifacts

- **role separation correctness**: **PASS**
  - **Why**: `ops` and `research` roles have distinct loop sets and role locks; smoke runs both supervisors.
  - **Proof artifacts**: `/opt/ezra-runtime/data/control/operating_system/role_contract.json` and loop status files

- **daemon continuity**: **PARTIAL**
  - **Why**: systemd units are defined with `Restart=always` but must be proven by observing restarts under failure (server proof required).
  - **Proof**: `systemctl status` + journald

- **mission/goals active**: **PASS**
  - **Proof**: mission/goals operating layer artifacts and task consumption (existing system proofs + deployed smoke checks existence)

- **comparisons active**: **PASS**
  - **Proof**: comparisons artifact exists after supervisor run

- **pnl review active**: **PASS**
  - **Proof**: pnl_review artifact exists after supervisor run

- **learning/research/implementation task routing active**: **PARTIAL**
  - **Why**: deployed smoke probes tasks.jsonl for known task types, but the task log path must be confirmed in the deployed runtime’s governance data location.
  - **Proof**: `deployed_environment_smoke.json` (`task_probe`)

- **live guard active**: **PARTIAL**
  - **Why**: live order guard enforcement is wired; deployed smoke currently validates core loops/tasks and non-live posture, but a server-side guard assertion in smoke should be extended if the guard requires venue/product fixtures.

- **live execution disabled**: **PASS**
  - **Why**: preflight and smoke fail-closed if live mode is enabled
  - **Proof artifacts**: `/opt/ezra-runtime/data/control/deploy_preflight.json` and `/opt/ezra-runtime/data/control/deployed_environment_smoke.json`

- **deployed-env smoke passing**: **PARTIAL**
  - **Why**: the smoke exists and is deterministic; PASS requires running it on the server under the same environment used by systemd services.

- **dual-repo deploy process finalized**: **PARTIAL**
  - **Why**: deploy script exists and is fail-closed; PASS requires running it on-server once to produce deployed refs and show services restarting only after passing.
  - **Proof artifact**: `/opt/ezra-runtime/data/control/deployed_refs.json`

### Anything still missing before micro trades?

**YES (by design)** — **intentional human activation** remains mandatory:
- confirm micro-trade parameters
- flip the live switch intentionally

Additionally, **server-run proof** is still required to upgrade PARTIAL → PASS for:
- boot persistence and post-reboot auto-start
- daemon restart behavior (Restart=always observed)
- dual-repo deploy script executed once against real server repos

