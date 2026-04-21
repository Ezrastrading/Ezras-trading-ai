## Final switch readiness audit (pre-live)

Truth version: `final_switch_readiness_audit_v1`

This audit is **strict**. Items are PASS only when there is:
- a **mechanism** in-repo, and
- a **server command** that produces a **machine-readable proof artifact** under `/opt/ezra-runtime/data/control/`.

### Key proof artifacts (required)

- `/opt/ezra-runtime/data/control/deploy_preflight.json`
- `/opt/ezra-runtime/data/control/deployed_environment_smoke.json`
- `/opt/ezra-runtime/data/control/micro_trade_readiness.json`
- `/opt/ezra-runtime/data/control/final_switch_readiness.json`

### Results (PASS / PARTIAL / FAIL)

- **public/private overlay correctness**: **PARTIAL**
  - **Mechanism**: `PYTHONPATH=/opt/ezra-private/trading-ai/src:/opt/ezra-public/trading-ai/src` in both systemd units.
  - **Proof**: run `deployed_environment_smoke.py` on the server and confirm `python_overlay.private_first == true`.

- **service persistence on boot**: **PARTIAL**
  - **Mechanism**: systemd unit templates + installer script exist.
  - **Proof required**: follow `docs/SERVICE_AND_BOOT_PROOF.md` and confirm post-reboot loop status timestamps advance.

- **role separation correctness**: **PASS**
  - **Mechanism**: `trading_ai.runtime.operating_system.role_contract()` and role locks.
  - **Proof**: `deployed_environment_smoke.json` shows both supervisors succeed and role contract exists.

- **daemon continuity under restart**: **PARTIAL**
  - **Mechanism**: `Restart=always`.
  - **Proof required**: server observation (`systemctl status` + journald) after intentional restart.

- **mission/goals active**: **PASS**
  - **Mechanism**: daily cycle hook refreshes mission/goals + consumes into tasks.
  - **Proof**: `deployed_environment_smoke.json` includes `mission_goals_plan` existence + task probe.

- **comparisons active**: **PASS**
  - **Mechanism**: research supervisor loop `comparisons`.
  - **Proof**: artifact exists in smoke.

- **pnl review active**: **PASS**
  - **Mechanism**: research supervisor loop `pnl_review` with risk task routing when negative.
  - **Proof**: `pnl_review.json` exists and task probe shows `pnl_review::risk_reduction` when applicable.

- **task routing + consumption**: **PASS**
  - **Mechanism**: task routing (`tasks.jsonl`) + task intake dispatch to `bot_inboxes`.
  - **Proof**: smoke requires `bot_inboxes.ok==true`.

- **live guard active**: **PARTIAL**
  - **Mechanism**: `trading_ai.nte.hardening.live_order_guard.assert_live_order_permitted()`.
  - **Proof required**: additional server-side guard proof can be run locally (no orders) and persisted as an artifact if desired.

- **live execution disabled**: **PASS**
  - **Mechanism**: hard defaults in systemd + runtime OS safety assert + preflight/smoke checks.
  - **Proof**: all four JSON gates report live disabled.

