# NTE hardening supplement (gap → implementation)

This README captures the **Section 1 gap analysis**, **missing modules**, **smoke tests**, **dry-run**, **acceptance criteria**, and **implementation order** for the follow-up hardening prompt. Code lives under `trading_ai/nte/` (not a monolith — layers stay separate).

## A. Gap analysis (likely missing / weak)

| Area | What is missing | Why it matters | Fix (this pass) | Criticality |
|------|-----------------|----------------|-----------------|-------------|
| Execution | Central order state machine, partial-fill handler | Prevents silent desync | Stub planned (`nte/execution/` extension) | Critical (future) |
| Risk | Explicit mode firewall for live | Accidental live orders | `mode_guard.py`, `mode_context.py` | Critical |
| Memory | Atomic writes, failure log | Torn files / no audit trail | `atomic_json.py`, `failure_guard.py` | Critical |
| Learning | Deduped trade IDs | Double-count lessons | Use trade `id` in append paths (future) | Important |
| Research | Promotion path to live | Silent strategy promotion | `research_firewall.py` + `promotion_log.json` | Critical |
| CEO | Action tracker JSON | No audit of follow-ups | `ceo_action_log.json` planned | Important |
| Reward | Decay / variance guards | Noisy rewards | `nte/rewards/engine.py` extension (future) | Important |
| Goal engine | Stale value detection | Wrong milestones | Health timestamps + goals_state | Important |
| Avenue isolation | Tests | Cross-avenue bleed | `test_nte_hardening_smoke.py` + avenue-scoped memory | Critical |
| Observability | Single health file | Ops blind | `system_health_reporter.py` | Critical |
| Testing | E2E dry run | No proof of wiring | `scripts/dry_run_smoke_test.py` | Critical |
| Deployment | Config fail-fast | Bad prod deploy | `config_validator.py` | Important |
| Failure recovery | Classified pause hints | Manual triage only | `FailureClass` + log | Important |
| Capital | Deposits vs PnL | Goal lies | `capital_ledger.py` | Critical |
| Config safety | Mode vs live flag | Replay + live | `validate_mode_safety` | Critical |

## B. Strengthened architecture (additions)

- **Failure layer**: `failure_guard.py` → `memory/failure_log.json`
- **Freshness**: `data_freshness_guard.py`
- **State validation**: `state_validator.py`
- **Memory integrity**: `memory_integrity_checker.py`
- **Mode safety**: `mode_context.py` + `mode_guard.py`
- **Health**: `reports/system_health_reporter.py` → `memory/system_health.json`
- **Capital truth**: `capital_ledger.py` → `memory/capital_ledger.json`
- **Research firewall**: `research/research_firewall.py` → `memory/promotion_log.json`
- **Config**: `config_validator.py`, `config_schema.py`
- **Reports**: `avenue_health_report`, `trade_audit_report`, `goal_progress_report`, `research_report`, `reward_report`

## C. Missing files (still to add in later passes)

- `nte/execution/order_state_machine.py`, `kill_switch.py`, `partial_fill_handler.py`
- `nte/global/reward_decay_engine.py` (or extend `nte/rewards/engine.py`)
- `nte/ceo/action_tracker.py` + `memory/ceo_action_log.json`
- `nte/global/projection_engine.py` + `memory/progress_path.json`
- `nte/memory/memory_backup_manager.py`, `memory_schema_registry.py` (extend `MemoryStore`)
- Full `tests/test_avenue_isolation.py`, `test_lesson_generalization.py`, `test_reward_scope.py`
- Wire `coinbase_engine` to call `assert_live_order_permitted` before orders (integration)

## D. Smoke test plan

- **Unit smoke**: `tests/test_nte_hardening_smoke.py` (config, integrity, health, mode, ledger, firewall)
- **Boot**: memory init + health refresh (`refresh_default_health`)
- **Future**: `test_smoke_system_boot.py`, iteration→CEO, goal update chains

## E. Dry-run script plan

- **Script**: `scripts/dry_run_smoke_test.py`
- **Behavior**: temp `EZRAS_RUNTIME_ROOT`, paper mode, simulated trades, ledger, firewall, live block, health

## F. Acceptance checklist (“fluid, set, working”)

- [x] Config validates (`validate_nte_settings`)
- [x] Memory initializes (`MemoryStore.ensure_defaults`)
- [x] System health file written (`refresh_default_health`)
- [x] Mock trade flow appends to `trade_memory` + ledger
- [x] Live orders impossible without `NTE_EXECUTION_MODE=live` + `NTE_LIVE_TRADING_ENABLED=true`
- [x] Research promotion logged and gated
- [x] Dry-run completes without credentials
- [ ] Full iteration engine + CEO action log wired (future)
- [ ] Order state machine + kill switches (future)

## G. Implementation order (recommended)

1. Mode + config validation (done)
2. Failure log + atomic JSON (done)
3. System health + capital ledger (done)
4. Research firewall (done)
5. Pytest smoke (done)
6. Dry-run script (done)
7. Wire live order guard into `nte/execution/coinbase_engine.py` (next)
8. CEO action tracker + order state machine (next)
9. Avenue isolation integration tests (next)
