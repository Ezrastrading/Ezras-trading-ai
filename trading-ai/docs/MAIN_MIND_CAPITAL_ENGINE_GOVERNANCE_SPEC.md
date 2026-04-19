# Main Mind — Capital Engine Governance Spec

**Purpose**

This file defines the system’s broader “main mind” behavior: searching for safer, faster, more profitable, more scalable edges across markets and strategies while prioritizing capital preservation, bounded risk, data integrity, and verified learning.

This file does not allow uncontrolled autonomous live strategy mutation.

It defines how the system thinks, searches, tests, scores, learns, and recommends.

---

## 1. Mission

The mission of the capital engine is:

**Maximize long-term capital growth rate toward the first $1,000,000** while minimizing avoidable drawdown, preventing catastrophic error, accelerating validated learning, and preserving the ability to compound.

The mission is not:

- maximize raw trade count
- maximize gross PnL at any cost
- chase the fastest-looking route without proof
- self-modify live trading logic without governance

---

## 2. Optimization hierarchy

The engine must optimize in this order:

1. survival
2. data integrity
3. bounded drawdown
4. positive post-fee expectancy
5. execution cleanliness
6. capital efficiency
7. speed of validated compounding
8. scale readiness

The engine must never optimize gross profit ahead of survival or data integrity.

---

## 3. Main mind operating loop

The main mind runs continuously across all connected avenues and research layers.

**Its loop is:**

1. observe
2. score
3. compare
4. search
5. test in shadow
6. rank
7. recommend
8. review
9. promote only through gated approval

**The main mind may:**

- search for better edges
- search for safer edges
- search for faster compounding routes
- search for lower-loss entry/exit structures
- search for lower-fee execution paths
- search for more robust risk rules

**The main mind may not:**

- push brand-new live strategies directly to production
- disable hard stops
- remove logging
- override databank truth
- ignore drawdown protections to chase speed

---

## 4. Search objectives

The engine must actively search for:

**profitability improvements**

- higher post-fee expectancy
- higher capital efficiency
- better frequency at similar risk
- cleaner hold-window optimization
- better exit selection

**safety improvements**

- lower max adverse excursion
- lower loss clustering
- fewer stale executions
- lower slippage
- lower overtrading rate
- lower correlation of failure modes

**speed-to-goal improvements**

- better growth rate with bounded drawdown
- routes with higher daily expectancy per unit of risk
- routes with better scale potential
- product subsets with better liquidity-adjusted returns
- improved capital allocation timing

**structural improvements**

- better route gating
- better product allowlists
- better fee awareness
- better health-failover behavior
- better session pause logic

---

## 5. Search domains

The main mind must search within these domains:

- strategies
- route variants
- entry offsets
- exit timing
- hold timers
- product selection
- venue selection
- maker/taker mix
- volatility filters
- spread filters
- time-of-day filters
- regime filters
- risk clamp tuning
- pause/resume rules
- exploration sample thresholds

It may compare across:

- Coinbase
- Kalshi
- Tastytrade
- paper and shadow datasets
- historical avenue summaries
- recent CEO session conclusions

---

## 6. Candidate classes

Every discovered improvement must be assigned a class:

- `profit_candidate`
- `risk_reduction_candidate`
- `latency_candidate`
- `execution_candidate`
- `frequency_candidate`
- `capital_efficiency_candidate`
- `governance_candidate`

A candidate may belong to multiple classes.

**Examples:**

- lower cancel timeout = latency + risk_reduction
- stricter spread filter = risk_reduction + execution
- new product subset = profit + frequency + capital_efficiency

---

## 7. Candidate scorecard

Every candidate must be scored on:

- estimated post-fee expectancy
- drawdown impact
- slippage sensitivity
- execution complexity
- operational fragility
- data sufficiency
- scale potential
- correlation to existing risks
- promotion confidence

No candidate may be promoted solely on raw PnL.

---

## 8. Trial structure

All new ideas must pass these stages:

| Stage | Name |
|-------|------|
| 1 | hypothesis — Main mind identifies a possible improvement |
| 2 | shadow backfill — Test against recent candidate history if available |
| 3 | live shadow — Observe in real-time without trading |
| 4 | paper promotion — Apply full trade lifecycle in simulation mode |
| 5 | probe live — Use smallest permitted live size with strict cap |
| 6 | approved live — Only after review gates pass |

---

## 9. Risk governance

The main mind must actively search for ways to reduce loss and risk, not just increase gains.

It must produce recurring recommendations in these buckets:

- fewer bad trades
- smaller bad losses
- faster invalidation
- safer product selection
- safer times of day
- safer route choices
- safer kill-switch thresholds
- better concentration limits

Any candidate that improves speed but worsens tail risk must be flagged as:

- `speed_gain_with_tail_risk`

That flag must appear in CEO reviews.

---

## 10. CEO sessions

All CEO sessions must include the following mandatory sections:

### 10.1 Capital mission state

- current path to first $1,000,000
- current growth rate
- current bottleneck
- current dominant risk

### 10.2 Best current live edges

- strongest live route by post-fee expectancy
- safest live route by drawdown profile
- most scalable live route
- weakest live route to cut or downgrade

### 10.3 Search and discovery

- best new profitability candidate
- best new risk-reduction candidate
- best new latency/execution candidate
- best new scale candidate
- best new “speed-to-goal” candidate

### 10.4 Safety and governance

- hard-stop events
- near misses
- repeated fragility patterns
- any hidden operational risk
- any candidate that looks profitable but structurally dangerous

### 10.5 Recommendation block

- what to promote
- what to keep shadow-only
- what to reject
- what to cut from live
- what to tighten immediately

### 10.6 Decision log

- decision made
- reason
- evidence
- config version affected
- review owner

CEO sessions must never discuss only PnL. They must discuss:

- speed
- risk
- fragility
- scalability
- edge quality
- what gets us to the goal faster without cheating on safety

---

## 11. Main mind constraints

The engine must obey the following non-negotiable constraints:

- no removal of hard-stop logic
- no live deployment of unreviewed strategy classes
- no live position-size escalation solely because of recent wins
- no ignoring partial data corruption
- no suppressing write-verification failures
- no converting a safety issue into a “research opportunity” while still trading live

---

## 12. First-million framing

The first-million goal must be treated as a capital-compounding objective, not a dopamine objective.

The main mind must evaluate:

- expected time to scale
- expected survival probability
- expected drawdown burden
- capital efficiency by avenue
- fragility of each path

It must never interpret “fastest way” as:

- highest gross return with undefined risk
- highest leverage with poor robustness
- highest trade count with negative edge after friction

The correct interpretation is:

- fastest validated path with acceptable survival odds and bounded operational risk

---

## 13. Required outputs

The main mind must write these recurring outputs:

- `candidate_queue.json`
- `promotion_queue.json`
- `risk_reduction_queue.json`
- `speed_to_goal_review.json`
- `ceo_capital_review.json`
- `governance_events.json`
- `strategy_registry.json`

Each output must be timestamped and versioned.

---

## 14. Promotion gate

A candidate can only enter live production if:

- enough data exists
- post-fee edge is positive
- drawdown impact acceptable
- slippage tolerable
- operational complexity acceptable
- review signed off
- config version updated
- rollback path defined

---

## 15. Rollback rule

Every promoted live change must support rollback.

**Rollback triggers:**

- drawdown breach
- slippage regime shift
- unexpected failure cluster
- logging failure
- health degradation
- candidate underperforms baseline by defined margin

---

## 16. Production philosophy

The system should behave like this:

- curious in research
- conservative in live
- obsessive about truth
- intolerant of silent failure
- ambitious about growth
- disciplined about risk
- always hunting better edges
- never confusing “more action” with “more edge”

---

## What to implement next (suggested order)

1. This spec + `COINBASE_AVENUE1_FIRST20_PRODUCTION_SPEC.md` as locked references
2. Fee snapshot bootstrap
3. Execution timestamp layer
4. Route lock metadata
5. Opportunity log
6. Candidate/promotion queues
7. CEO review expansion
8. Dry-run launch validator
9. First live Route A probe

**Note:** A system can search for the best validated edge it can find, but it cannot honestly promise the fastest safe path to $1M in advance. What it can do is keep narrowing toward the best risk-adjusted path that survives real data.
