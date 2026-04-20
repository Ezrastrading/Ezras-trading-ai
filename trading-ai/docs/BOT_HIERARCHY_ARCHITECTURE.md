# Bot hierarchy architecture

## Levels

| Level | Role | Authority |
|-------|------|-----------|
| 0 | **Ezra governor** (`ezra_governor`) | Top governance intelligence; does not place venue orders. |
| 1 | **Avenue master** (`avenue_master`) | One per avenue id (string, e.g. `A`, `kalshi`, `tastytrade`). Aggregates gate intelligence; proposes research. |
| 2 | **Gate manager** (`gate_manager`) | One per `(avenue_id, gate_id)`. Owns hypotheses, evidence requests, worker coordination. |
| 3 | **Gate worker** (`gate_worker`) | Narrow tasks only; reports structured outputs upward. |

## Storage

Default root: `src/trading_ai/global_layer/_governance_data/bot_hierarchy/` (override with `EZRAS_BOT_HIERARCHY_ROOT`).

Canonical state: `hierarchy_state.json`. Derived artifacts (same root): `bot_registry.json`, `avenue_master_state.json`, `gate_manager_state.json`, `worker_bot_state.json`, `bot_relationship_graph.json`, `gate_candidates.json`.

Reports (JSONL): `reports/bot_report.jsonl`, `avenue_master_reports.jsonl`, `gate_manager_reports.jsonl`, `worker_reports.jsonl`, `gate_research_reports.jsonl`.

Knowledge (JSON): `knowledge/avenue_master_knowledge.json`, `gate_mastery_index.json`, `gate_lessons_index.json`, `strategy_knowledge_index.json`, `venue_mechanics_index.json`.

## Orchestration registry

The file-backed **orchestration** `bot_registry.json` (multi-bot execution governance) is **separate**. Hierarchy bots do not receive live permissions from this layer; linking is optional via `linked_orchestration_bot_id` when needed.

## Invariants

- All hierarchy `live_permissions` remain false (`venue_orders`, `runtime_switch`, `capital_allocation_mutate`).
- `can_modify_live_logic` is always false on hierarchy records.
- Command-down / report-up is represented by `parent_bot_id` and reporting artifacts; nothing here bypasses promotion or execution authority contracts.
