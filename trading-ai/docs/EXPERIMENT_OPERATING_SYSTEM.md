# Experiment operating system

**Purpose:** Registry + JSONL results for structured experiments (replay, sim, variants, candidate gate probes).

**Does not:** Create live permissions or bypass the promotion ladder. Experiments are advisory records.

**Artifacts:** `experiment_registry.json`, `experiment_results.jsonl`, `experiment_summary_by_gate.json`, `experiment_summary_by_avenue.json`.

**Commands:**
- `python -m trading_ai.deployment experiment-status-report`
- Use `trading_ai.org_organism.experiment_os.register_experiment` from allowed contracts only (no auto-live).

**Lifecycle:** Define hypothesis, success/stop criteria, max duration/samples; append results; feed promotion decisions only through normal proof + governance paths.
