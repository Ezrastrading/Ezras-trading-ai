# AI Review Execution Layer

Runtime behavior contract for the automated review board. **Implementation:** `trading_ai/global_layer/` — `review_prompts.py`, `review_schema.py` (validation + `whitelist_model_output` for contract-only keys), `claude_review_runner.py`, `gpt_review_runner.py`, `joint_review_merger.py`, `review_confidence.py`, `review_action_router.py` (rejects `FORBIDDEN_ACTION_TYPES` in `_log_action`), `ceo_review_writer.py`, `review_retry_policy.py`, `review_integrity.py`, `queue_priority_refresh.py`, `governance_order_gate.py`. Packet builder adds `packet_truth` (federated trade ingest) and mirrors `hard_stop_events` / `max_anomaly_severity` into `risk_summary` for ranker/merger. **`review_packet_expander` removed** (was dead code).

**Downstream order gating (bounded):** `governance_order_gate.check_new_order_allowed` is used by Shark `run_execution_chain` (gate 1b) and NTE Coinbase `_maybe_enter`. Default is **advisory-only** (no blocking). With enforcement on: **paused** → fail-closed; **caution** → fail-closed only if `GOVERNANCE_CAUTION_BLOCK_ENTRIES=true`; **missing/empty joint**, **stale** (vs `GOVERNANCE_JOINT_STALE_HOURS`), **unknown mode**, **degraded integrity** → fail-open unless the matching `GOVERNANCE_*_BLOCKS` env is set (see `governance_order_gate` module docstring). All paths are INFO/WARN logged — no silent blocks.

## Core principle

Same packet to both models; short structured JSON only; schema-validated; deterministic merger; governance-constrained routing; unsafe recommendations logged under `changes_blocked`, not auto-applied as forbidden actions.

## Review flow

1. Build packet → validate (implicit via builder)  
2. Claude → validate output → repair once if invalid  
3. GPT → validate output → repair once if invalid  
4. Merge → joint confidence → safe actions → queues → CEO summary (EOD/exception) → persist  

If a model fails after retry: mark unusable; **never fabricate** that model’s output. Single-model fallback is **degraded** (`review_integrity_state`).

## Prompts

Exact strings live in `review_prompts.py`:

- **Claude:** `CLAUDE_SYSTEM_PROMPT` + `claude_user_prompt(packet)` (JSON body).
- **GPT:** `GPT_SYSTEM_PROMPT` + `gpt_user_prompt(packet)`.
- **Repair:** `REPAIR_PROMPT` (one attempt after invalid JSON/schema).

## Output contracts

Validated by `review_schema.validate_claude_output` / `validate_gpt_output`:

- Claude: `what_is_working`, `what_is_not_working`, risk/fragility fields, `risk_mode_recommendation` ∈ {normal,caution,paused}, `confidence_score` ∈ [0,1], matching `packet_id` / `review_type`.
- GPT: `top_3_*`, `live_status_recommendation`, edges, bottleneck, `short_ceo_note`, `confidence_score` ∈ [0,1].

Joint output (`joint_review_merger`): `joint_review_id`, `packet_id`, `claude_review_id` | null, `gpt_review_id` | null, `review_integrity_state`, `house_view`, `live_mode_recommendation`, `changes_recommended`, `changes_blocked`, `ceo_summary`, `path_to_first_million_summary`, `confidence_score`.

Action log (`review_action_router`): `action_id`, `joint_review_id`, `packet_id`, `ts`, `action_type`, `target`, `reason`, `evidence_refs`, `applied`, `blocked`, `block_reason`.

## Merger priorities

1. Packet hard facts  
2. Shared agreement  
3. Claude: risk / fragility  
4. GPT: ranking / prioritization  
5. Conservative on disagreement (`paused` > `caution` > `normal`)

## Safe action router

Allowed: caution flags, live-mode recommendation signals, queue priority, CEO/governance notes, extra review, manual attention, shadow-only markers, reduced activity — **never** direct new live strategy deploy, core code change, size beyond policy, hard-stop off, verification override.

## Integrity

- `full`: packet valid + both models validated  
- `degraded`: one model validated  
- `failed`: packet invalid or neither model usable  

Degraded joint confidence is capped (see governance formulas spec).

## Tests

`tests/test_ai_review_orchestration.py` — packet build, stub cycle, merge, router, schema, disagreement/pause, confidence caps.
