# Avenue master contract (code + behavior)

## Responsibilities

Implemented as typed contract summaries in `bot_hierarchy/contracts.py`:

- Learn venue mechanics; maintain mastery indexes under `knowledge/`.
- Aggregate and compare gate intelligence; flag weak or missing gates.
- Propose new gates into **research** only (`discover_gate_candidate`).
- Teach / support gate managers via `emit_guidance_downstream` (advisory; no permission changes).
- Summarize priorities upward (Ezra / CEO artifacts).

## Forbidden

- Self-grant live permissions.
- Bypass orchestration promotion ladder or execution authority.
- Treat manager reports as runtime proof unless `is_runtime_proof` is true and backed by the existing artifact chain (default: advisory-only reports).

## Gate manager (reference)

End-to-end ownership of one gate: strategy shape, constraints, failure modes, worker coordination, pass/fail **recommendations** for promotion steps — still subject to deterministic promotion evaluation elsewhere.

## Workers

Single narrow job; structured JSON outputs; report to gate manager only.

## Enforcement

- Pydantic validators on `HierarchyBotRecord` enforce false live flags and no `can_modify_live_logic`.
- `guards.assert_hierarchy_bot_no_live_authority` on registry save path for hierarchy bots.
