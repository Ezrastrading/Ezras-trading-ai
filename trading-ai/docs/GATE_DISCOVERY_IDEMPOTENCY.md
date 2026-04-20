# Gate discovery idempotency

`discover_gate_candidate` computes a `canonical_fingerprint` from avenue, gate, thesis, edge hypothesis, execution path, limits, and constraints.

- **Same fingerprint:** refreshes `last_seen_at` on the existing candidate — no duplicate row.
- **New fingerprint, same avenue+gate:** appends a new candidate with `supersedes_candidate_id` pointing at the latest prior row for that pair.
