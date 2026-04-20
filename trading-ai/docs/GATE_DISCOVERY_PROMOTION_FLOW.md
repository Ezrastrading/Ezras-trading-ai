# Gate discovery and promotion flow

## Gate candidate lifecycle (no skips)

Ordered stages (advance one step at a time via `advance_gate_candidate_stage`):

1. `discovered`
2. `documented`
3. `hypothesis_defined`
4. `replay_ready`
5. `replay_tested`
6. `sim_candidate`
7. `sim_passed`
8. `staged_runtime_candidate`
9. `staged_runtime_passed`
10. `supervised_live_candidate`
11. `supervised_live_passed`
12. `autonomous_candidate`
13. `autonomous_approved`
14. `autonomous_live_enabled`

Skipping a stage raises `hierarchy_guard:stage_skip_forbidden`.

## Relationship to orchestration promotion

- **Gate-candidate stages** document research and intended progression. They do **not** grant `PermissionLevel` or venue execution.
- **Runtime promotion** remains on existing paths: orchestration tiers (`T0`–`T5`), `promotion_queue`, execution authority registry, staged validation, supervised proofs, autonomous contracts — unchanged.
- Mapping reference: `ExecutionRung` ↔ `PromotionTier` in `lock_layer/promotion_rung.py`; gate-candidate stages are parallel documentation until backed by the same proof artifacts the orchestration layer already requires.

## Entry points

- `discover_gate_candidate` / CLI `discover-gate-candidate`: creates candidate + gate manager + suggested workers (all hierarchy, research posture).
- `build_gate_candidate_from_review_stub` / CLI `build-gate-candidate-from-review`: stub from review text — still research-only.
- `promote-gate-candidate-report` / CLI: prints current stage, next stage, blockers — **does not** promote live systems.

## Language

Hypotheses and “expected PnL shape” fields are **descriptive**; the codebase avoids profit certainty. Evidence lives in `evidence_refs` and existing proof JSON under runtime/control as wired elsewhere.
