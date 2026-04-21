## FINAL PRE-LIVE GAP AUDIT (brutal)

Truth version: `final_prelive_gap_audit_v1`

This audit is **strict**. “PASS” requires:
- an always-on daemon/supervisor mechanism (not “run once in shell”), and
- durable artifacts on disk proving the loop is executing, and
- non-live safety remains fail-closed.

Where this audit says PARTIAL/FAIL, it is naming *exact* missing proof or missing consumption.

### Evidence anchors (authoritative code)

- **Operating system supervisor (non-live, role separated)**: `trading-ai/src/trading_ai/runtime/operating_system.py`
  - `try_acquire_role_lock`, `release_role_lock`
  - `run_role_supervisor_once` (writes per-loop artifacts + per-role status)
  - `_ops_loops()` and `_research_loops()`
- **Runtime entrypoint**: `trading-ai/src/trading_ai/runtime/__main__.py` (`python -m trading_ai.runtime daemon --role ...`)
- **Task routing (append-only audit stream)**: `trading-ai/src/trading_ai/global_layer/task_router.py`, `task_registry.py`
- **Task consumption (durable per-bot inbox)**: `trading-ai/src/trading_ai/global_layer/task_intake.py`
- **Mission/goals as active driver**: `trading-ai/src/trading_ai/global_layer/mission_goals_operating_layer.py` + `mission_goals_task_consumer.py`
- **Live order guard (must block unless live enabled)**: `trading-ai/src/trading_ai/nte/hardening/live_order_guard.py`
- **Server proof scripts**: `trading-ai/scripts/server/*.py`, `*.sh`

### PASS / PARTIAL / FAIL audit (25 items)

1. **Continuous scanning autonomy**: **PASS**
   - **Mechanism**: ops supervisor loop `scanner_cycle` in `_ops_loops()`.
   - **Durable proof**: per-loop result artifact at `.../data/control/operating_system/loops/ops/scanner_cycle.json` and status in `loop_status_ops.json`.

2. **Continuous research autonomy**: **PARTIAL**
   - **Mechanism**: research supervisor runs `daily_cycle`, `comparisons`, `trade_cycle_intelligence`, etc.
   - **Gap**: “research” is currently deterministic/stubbed in `tick_research_once(...skip_models=True)`; external-model research is intentionally not exercised in server smoke.
   - **Proof**: `loop_status_research.json` + per-loop artifacts.

3. **Continuous learning autonomy**: **PARTIAL**
   - **Mechanism**: `learning_distillation_snapshot` loop exists.
   - **Gap**: distillation snapshot is a snapshot; approval remains explicitly gated (by design) and not auto-applied.
   - **Proof**: `.../learning_distillation_snapshot.json` and loop artifacts.

4. **Continuous self-review autonomy**: **PASS**
   - **Mechanism**: research loop `review_cycle` calls `run_full_review_cycle(...skip_models=...)` and persists `joint_review_latest.json` (review storage).
   - **Proof**: research loop result artifacts + review storage outputs (runtime dependent).

5. **Continuous self-audit autonomy**: **PARTIAL**
   - **Mechanism**: ops `fast_health_snapshot` writes orchestration chain blockers; research loops write governance snapshots.
   - **Gap**: a single canonical “self-audit verdict” artifact is not currently required by readiness gates (it can be added later).

6. **Continuous PnL evaluation autonomy**: **PASS**
   - **Mechanism**: research loop `pnl_review` writes `data/control/pnl_review.json`.
   - **Proof**: `pnl_review.json` exists after supervisor.

7. **Continuous profitability/strategy comparison autonomy**: **PASS**
   - **Mechanism**: research loop `comparisons` computes avenue performance and writes `performance_comparisons.json`.
   - **Behavioral consumption**: routes `comparisons::avenue` tasks (shadow routing).
   - **Proof**: artifact + tasks stream.

8. **Continuous regression/drift detection autonomy**: **PARTIAL**
   - **Mechanism**: ops loop `fast_regression_drift` writes `data/control/ops_regression_drift.json`.
   - **Gap**: drift outputs are not yet routed into corrective tasks (can be wired later).

9. **Continuous mission/goals progression autonomy**: **PASS**
   - **Mechanism**: daily cycle hook refreshes mission/goals plan + seeds queues; consumer converts to tasks.
   - **Proof**: `mission_goals_operating_plan.json` + tasks.

10. **Continuous task routing autonomy**: **PASS**
   - **Mechanism**: routing via `route_task_shadow` from mission/goals + pnl/comparisons.
   - **Proof**: append-only `tasks.jsonl` in runtime governance dir.

11. **Continuous bot-level work assignment**: **PASS**
   - **Mechanism**: `route_task_shadow` assigns to `assigned_bot_id` using `pick_primary_bot`.
   - **Proof**: tasks rows contain `assigned_bot_id`.

12. **Continuous avenue-level work assignment**: **PASS**
   - **Mechanism**: routing tasks carry `avenue` field; consumers route across (avenue, gate) scopes.
   - **Proof**: tasks rows include avenue/gate.

13. **Continuous gate-level work assignment**: **PASS**
   - **Mechanism**: routing includes `gate`; mission consumer routes across discovered scopes.
   - **Proof**: tasks rows include `gate` and per-bot inbox path uses assigned bot.

14. **CEO session/review continuity**: **PARTIAL**
   - **Mechanism**: research tick writes CEO daily review (`write_daily_ceo_review`).
   - **Gap**: durable “CEO session continuity” artifact is not currently a readiness hard-requirement.

15. **Durable runtime artifacts for every major loop**: **PASS**
   - **Mechanism**: supervisor writes `loop_status_<role>.json` + per-loop result JSON.

16. **Durable per-loop status**: **PASS**
   - **Mechanism**: `loop_status_ops.json` and `loop_status_research.json` include loop metadata and last_run timestamps.

17. **Durable per-role status**: **PASS**
   - **Mechanism**: per-role status artifacts are written each supervisor run.

18. **Boot persistence**: **PARTIAL**
   - **Mechanism**: systemd unit templates + installer exist.
   - **Missing proof**: must be run on the actual server and verified post-reboot per `docs/SERVICE_AND_BOOT_PROOF.md`.

19. **Service persistence**: **PARTIAL**
   - **Mechanism**: `Restart=always` in systemd units.
   - **Missing proof**: server observation under restart and after failure.

20. **Server role separation**: **PASS**
   - **Mechanism**: `ops` and `research` roles have disjoint loops and independent role locks.
   - **Proof**: `role_contract.json` + `server_role_locks.json`.

21. **Public/private overlay stability**: **PARTIAL**
   - **Mechanism**: PYTHONPATH overlay set in systemd units.
   - **Missing proof**: must run deployed smoke on server to record `python_overlay` truth.

22. **Deployed-env smoke completeness**: **PASS (mechanism), PARTIAL (server proof)**
   - **Mechanism**: `scripts/server/deployed_environment_smoke.py` writes JSON.
   - **Missing proof**: must run on server and save artifact at `/opt/ezra-runtime/...`.

23. **Micro-trade readiness proof**: **PASS (mechanism), PARTIAL (server proof)**
   - **Mechanism**: `scripts/server/micro_trade_readiness.py` writes JSON gate.
   - **Missing proof**: run on server.

24. **Anything still interactive/manual that should be autonomous**: **PARTIAL**
   - **Found**: server boot/restart proof still requires server execution (expected).
   - **Found**: model-backed review/research is intentionally stubbed in non-live OS smoke.

25. **Anything still only exists in tests/mock but not real runtime consumption**: **PASS**
   - **Mechanism**: task intake dispatch loop converts routed tasks to durable per-bot inbox artifacts.
   - **Proof**: `data/control/bot_inboxes/*.json` required by smoke + micro-trade readiness.

### Bottom line

- **Local repo wiring**: strong.
- **Deployed proof**: remains **PARTIAL until you run the server commands** (by design; can’t be faked locally).

