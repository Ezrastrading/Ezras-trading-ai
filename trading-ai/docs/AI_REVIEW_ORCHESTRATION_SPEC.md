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
| `ai_review_packet_builder.py` | Build `review_packet_latest.json` |
| `review_packet_expander.py` | Anomaly-only deeper packet |
| `claude_review_runner.py` | Claude → `claude_review_latest.json` |
| `gpt_review_runner.py` | GPT → `gpt_review_latest.json` |
| `joint_review_merger.py` | Merge → `joint_review_latest.json` |
| `review_scheduler.py` | When to run; dedupe spam |
| `review_action_router.py` | Safe queue updates + action log |

---

## 5. Global memory outputs

**JSON:** `review_packet_latest.json`, `claude_review_latest.json`, `gpt_review_latest.json`, `joint_review_latest.json`, `review_scheduler_state.json`, `review_policy_snapshot.json`, `ceo_capital_review.json`, `first_million_progress_review.json`, queue files (`candidate_queue.json`, etc.)

**JSONL:** `review_packet_history.jsonl`, `claude_review_history.jsonl`, `gpt_review_history.jsonl`, `joint_review_history.jsonl`, `review_action_log.jsonl`, `review_anomaly_packets.jsonl`

---

## 6. Schemas

See in-code defaults in `review_storage.py` and runner output validators. Packet includes: `capital_state`, `avenue_state`, `live_trading_summary`, `route_summary` (A/B), `risk_summary`, `shadow_exploration_summary`, `goal_state`, `lesson_state`, `review_context_rank`.

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
6. `review_packet_expander`  
7. Integration tests  

**V1 ships:** build packet → Claude → GPT → merge → persist → optional safe actions. **V2:** richer anomaly routing and CEO section automation.

---

## Operating truth

Models are the **AI Review Board** — not uncontrolled CEOs. Promotions to live still require evidence thresholds, governance, config versioning, and rollback paths.
