# AI Review Orchestration — Production Spec

**Purpose**

This spec defines the automated **Claude + GPT** review layer for the **entire organism** (all avenues). It is advisory: models **review, rank, warn, recommend, teach** — they do **not** silently deploy live strategies, disable hard stops, override logging, or raise risk beyond policy.

**Scope:** Global; not Coinbase- or Kalshi-specific. Evidence comes from compressed packets built from trade summaries, monitoring, queues, goals, and learning hooks.

---

## 0. Mission

Answer several times per day: *What works? What fails? What is fragile? What is safest? What is most scalable? What is the strongest validated path toward $1,000,000? What stays live? What to cut, pause, tighten, or keep shadow-only?*

Behave like an expert board, not a hype machine.

---

## 1. Design rules

- **Evidence first** — trades, scores, risk events, monitoring, shadow candidates, progress state.
- **Low token cost** — compact packets; expand only on anomaly.
- **Short outputs** — concise, decision-oriented JSON.
- **Dual-model** — Claude + GPT independently; **joint** house view merged.
- **Safe downstream actions only** — queue priorities, caution, recommendations; no auto live promotion of new strategy classes.

---

## 2. Cadence

| Review | Purpose |
|--------|---------|
| Morning | Overnight performance, what should be live today, main risk/opportunity |
| Midday | Execution quality, drift; **skip** if activity below threshold |
| EOD CEO | Day summary, lessons, first-million path, governance queues |
| Exception | Hard-stop, write failure, slippage/loss/WS clusters — rare |

---

## 3. Model roles

- **Claude** — risk chair, fragility, false edges, “what is fooling us?”
- **GPT** — CEO advisor, prioritization, top actions, executive summary
- **Joint** — balanced, evidence-weighted house view

---

## 4. Code modules (`trading_ai/global_layer/`)

| Module | Role |
|--------|------|
| `review_storage.py` | JSON / JSONL paths, defaults, append history |
| `review_policy.py` | Cadence, thresholds, permissions |
| `review_context_ranker.py` | Prioritize facts; keep packets small |
| `ai_review_packet_builder.py` | Build `review_packet_latest.json`; `scheduler_gates_snapshot()` for fresh scheduler inputs |
| `claude_review_runner.py` | Claude → `claude_review_latest.json` |
| `gpt_review_runner.py` | GPT → `gpt_review_latest.json` |
| `joint_review_merger.py` | Merge → `joint_review_latest.json` |
| `review_scheduler.py` | When to run; dedupe spam |
| `review_action_router.py` | Safe queue updates + action log |
| `trade_truth.py` | Federated trade list (NTE memory + databank) + fairness meta |
| `queue_priority_refresh.py` | Applies `governance_formulas` scores + `promotion_gates_ok` to queue JSON ordering |
| `governance_order_gate.py` | Optional order enforcement from `joint_review_latest` (default: advisory-only) |

---

## 5. Global memory outputs

**JSON:** `review_packet_latest.json`, `claude_review_latest.json`, `gpt_review_latest.json`, `joint_review_latest.json`, `review_scheduler_state.json`, `review_policy_snapshot.json`, `ceo_capital_review.json`, `first_million_progress_review.json`, queue files (`candidate_queue.json`, etc.)

**JSONL:** `review_packet_history.jsonl`, `claude_review_history.jsonl`, `gpt_review_history.jsonl`, `joint_review_history.jsonl`, `review_action_log.jsonl`, `review_anomaly_packets.jsonl`

---

## 6. Schemas

See in-code defaults in `review_storage.py` and runner output validators. Packet includes: `capital_state`, `avenue_state`, `live_trading_summary`, `route_summary` (neutral bucket rollups — **schema v2**, see below), `risk_summary`, `shadow_exploration_summary`, `goal_state`, `lesson_state`, `review_context_rank`, `packet_truth` (documented limitations of ingest).

**`route_summary` (universal core):** `schema_version` **2.0** — `buckets` maps opaque bucket ids (from `route_bucket` / `route_label` / `strategy_class` / `setup_type` fallbacks) to the same numeric stats the old packet used per slice. There is **no fixed `route_a` / `route_b` organism architecture**; avenue-specific or strategy-family labels are carried as metadata only. Optional `merge_note` appears when many distinct buckets are collapsed into `_other_merged` for packet size.

Claude / GPT / joint outputs are short JSON objects with fields listed in the implementation (risk notes, top 3 actions, `live_mode_recommendation`, etc.).

---

## 7. Action router — allowed vs forbidden

**Allowed:** caution flags, queue priority, CEO queue notes, governance notes, recommend pause / reduced mode / shadow-only, request extra review.

**Forbidden:** deploy new live strategy classes, disable hard stops, size up beyond policy, ignore verification, auto-promote shadow → full live.

---

## 8. Failure handling

Preserve packet on model failure; log; retry per policy. Joint merge failure → mark layer degraded; do not fabricate fields.

---

## 9. Testing

Packet build (empty / normal / anomaly), runners (stub path), merger, scheduler thresholds, action router safety.

---

## 10. Implementation order

1. `review_storage` + `review_policy`  
2. `ai_review_packet_builder` + `review_context_ranker`  
3. `claude_review_runner` + `gpt_review_runner`  
4. `joint_review_merger`  
5. `review_scheduler` + `review_action_router`  
6. Integration tests  

**Runtime ownership:** `tick_scheduler()` is invoked from the Shark daemon on an interval (default: every **20 minutes**, env `AI_REVIEW_TICK_MINUTES`; disable with `AI_REVIEW_TICK_ENABLED=false`). It uses `scheduler_gates_snapshot()` so gate inputs are not read from a stale `review_packet_latest.json`.

**Order gating:** Joint review affects real order entry only when `GOVERNANCE_ORDER_ENFORCEMENT=true` (otherwise advisory-only). Coinbase NTE (`_maybe_enter`) and Shark `run_execution_chain` (gate 1b) call `governance_order_gate.check_new_order_allowed` with venue + route. **Paused** blocks new entries when enforcement is on. **Caution** blocks only if `GOVERNANCE_CAUTION_BLOCK_ENTRIES=true`. **Missing/empty joint**, **stale joint** (`GOVERNANCE_JOINT_STALE_HOURS`, default 168h), **unknown live_mode**, and **degraded review_integrity** default to **fail-open** (allow + WARNING log) unless the corresponding strict env is set: `GOVERNANCE_MISSING_JOINT_BLOCKS`, `GOVERNANCE_STALE_JOINT_BLOCKS`, `GOVERNANCE_UNKNOWN_MODE_BLOCKS`, `GOVERNANCE_DEGRADED_INTEGRITY_BLOCKS`.

**Scheduler audit:** Each `tick_scheduler` evaluation appends two lines to `review_scheduler_ticks.jsonl` (evaluate + complete) with fresh `scheduler_gates_snapshot` inputs and suppress flags.

**Kalshi / multi-avenue truth:** Federated trades prefer NTE `trade_memory` + databank JSONL. Kalshi rows typically appear as **databank-only** until a full Shark→databank close pipeline exists; `packet_truth.avenue_representation` and warnings flag **missing** expected Kalshi coverage. Play-money venues (e.g. Manifold) are labeled via `unit=play_money` in federated rows where applicable.

**V1 ships:** build packet → Claude → GPT → merge → persist → optional safe actions. **V2:** richer anomaly routing and CEO section automation.

---

## Operating truth

Models are the **AI Review Board** — not uncontrolled CEOs. Promotions to live still require evidence thresholds, governance, config versioning, and rollback paths.
