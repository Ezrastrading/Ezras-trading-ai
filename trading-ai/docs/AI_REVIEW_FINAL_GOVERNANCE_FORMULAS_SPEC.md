# AI Review Final Governance Formulas

Hard-number layer: queues, anomalies, joint confidence, scheduler defaults. **Implementation:** `governance_formulas.py`, `review_confidence.py`, `review_policy.py`, `review_scheduler.py`.

## Queue priority formulas

All scores clamped **0–100**. Labels: 0–24 low, 25–49 medium, 50–74 high, 75–100 critical.

| Queue | Function |
|-------|----------|
| Candidate | `candidate_priority_score` — weighted post-fee expectancy, risk reduction, execution, sample, path-to-goal, scalability, minus fragility/novelty/regime penalties |
| Promotion | `promotion_priority_score` — blends candidate score with shadow validation, drawdown, readiness, governance, minus regime/model disagreement |
| Risk reduction | `risk_reduction_priority_score` — optional +10 escalation bonus when verification/slippage/WS/loss clusters apply |
| CEO review | `ceo_review_priority_score` |
| Speed-to-goal | `speed_to_goal_priority_score` — minus drawdown burden |

**Promotion gates:** `promotion_gates_ok()` — thresholds on candidate/shadow/drawdown/governance, no paused live mode, no verification failure, no hard-stop cluster.

## Anomaly severity

Helpers: `anomaly_severity_label`, `ws_stale_severity_market`, `ws_stale_severity_user`. Aggregate: `compute_anomaly_aggregate_score` in `review_confidence.py` (packet + optional components).

Default thresholds (see `PRODUCTION_DEFAULTS` in `governance_formulas.py`): market WS 15/30/60s; user WS 20/45/90s; exception review cooldown 45 min; max reviews/day 4; joint confidence caution 0.55, pause attention 0.40; promotion min priority 65; risk reduction escalation bonus 10.

## Packet completeness

`compute_packet_completeness_score(packet)` — ten sections aligned to `ai_review_packet_builder` keys (capital, avenue_state, live, risk, route, shadow, goal, lesson, review_context_rank, verification signal from `risk_summary`).

## Model agreement

`compute_agreement_score(claude, gpt)` — compare live mode, risk vs warnings, path vs bottleneck, edge alignment, next-action vs safe improvement; average × 100.

## Joint confidence

`compute_joint_confidence(...)` — weights: 22% Claude, 22% GPT, 20% packet completeness, 14% agreement, 12% sample strength, minus 10% anomaly aggregate (all 0–100 scale internally where applicable). Stored **0.0–1.0**.

**Caps:** failed integrity → 0; degraded → max 0.74; completeness &lt; 60 → max 0.59; anomaly aggregate &gt; 75 → max 0.49; live-mode disagreement with anomaly &gt; 50 → max 0.44.

## Sample strength

`sample_strength_from_trade_count` / `sample_strength_from_packet` — trade-count buckets 0–2 … 40+.

## Scheduler

`review_scheduler.py` — morning/midday/EOD gates; `tick_scheduler` uses packet-derived activity. Align env `AI_REVIEW_MAX_PER_DAY` with defaults (4). Midday gates: min closed trades, shadow candidates, anomaly count (policy).

## Tests

Formula branches covered in `test_ai_review_orchestration.py` (joint confidence, merge under risk). Extend with dedicated formula tests as needed.
