"""CLI: python -m trading_ai institutional <subcommand> [...]"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable, Dict, List, Optional


def _pj(x: Any) -> None:
    print(json.dumps(x, indent=2, default=str))


def main_institutional(argv: Optional[List[str]]) -> int:
    p = argparse.ArgumentParser(prog="institutional")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name: str, fn: Callable[[argparse.Namespace], int], **kw: Any) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, **kw)
        sp.set_defaults(_fn=fn)
        return sp

    add("oms-status", lambda ns: _oms_status())
    sp = add("show-intent", lambda ns: _show_intent(ns))
    sp.add_argument("--intent-id", default="demo-intent")
    sp = add("show-order-genealogy", lambda ns: _genealogy(ns))
    sp.add_argument("--intent-id", default="demo-intent")
    add("show-execution-alpha", lambda ns: _exec_alpha())
    sp = add("show-execution-forensics", lambda ns: _forensics(ns))
    sp.add_argument("--incident-id", default="f1")
    add("show-temporal-concentration", lambda ns: _temporal())
    add("show-execution-batch", lambda ns: _batch())
    add("show-netting-report", lambda ns: _netting())
    add("show-arbitration-queue", lambda ns: _arb())
    add("show-model-inventory", lambda ns: _models())
    add("show-unchallenged-models", lambda ns: _unchallenged())
    sp = add("run-model-challenge", lambda ns: _challenge(ns))
    sp.add_argument("--model-id", default="dqs_engine_v1")
    add("show-external-assumptions", lambda ns: _assumptions())
    add("show-research-pipeline", lambda ns: _research())
    add("show-false-discovery-risk", lambda ns: _fdr())
    add("show-hypothesis-registry", lambda ns: _hyp())
    add("show-efficiency-frontier", lambda ns: _frontier())
    add("show-fragility-report", lambda ns: _fragility())
    add("show-crowding-warnings", lambda ns: _crowd())
    add("show-alpha-forecasts", lambda ns: _forecast())
    add("show-decay-halflife", lambda ns: _halflife())
    sp = add("run-adversarial-review", lambda ns: _adv(ns))
    sp.add_argument("--strategy-id", default="strat-007")
    add("show-governance-capture-status", lambda ns: _gc())
    add("show-adversarial-findings", lambda ns: _pj({"findings": []}))
    add("show-risk-committee-report", lambda ns: _risk_comm())
    add("show-post-mortems", lambda ns: _pms())
    add("show-regime-playbooks", lambda ns: _playbooks())
    add("show-liquidity-reserve", lambda ns: _reserve())
    add("show-meta-attribution", lambda ns: _meta_attr())
    add("show-human-alpha", lambda ns: _human())
    add("show-complexity-justification", lambda ns: _complexity())
    sp = add("run-compound-scenario", lambda ns: _scenario(ns))
    sp.add_argument("--scenario-id", default="compound_1")
    add("show-scenario-results", lambda ns: _pj({"note": "see scenario_results_path"}))
    add("status", lambda ns: _status())
    add("validate-institutional", lambda ns: _validate())
    add("run-institutional-gap-check", lambda ns: _gap())
    add(
        "activation-seed",
        lambda ns: _activation_seed(),
        help="Safe local paths to record all automation heartbeats + temporal samples",
    )
    add(
        "activation-flow",
        lambda ns: _activation_flow(),
        help="One supervised end-to-end safe activation (no live orders)",
    )
    add(
        "final-readiness-audit",
        lambda ns: _final_readiness_audit(),
        help="Whole-system pre-test audit (run after activate + seed)",
    )
    add(
        "smoke-readiness",
        lambda ns: _smoke_readiness(),
        help="Aggregated smoke of activation primitives",
    )
    add(
        "controlled-backend-test",
        lambda ns: _controlled_backend_test(),
        help="Full controlled backend validation (safe, isolated temp runtime by default)",
    )

    ns = p.parse_args(argv or [])
    return int(ns._fn(ns))


def _oms_status() -> int:
    from trading_ai.phase_institutional.oms import order_registry

    _pj({"active_orders": order_registry.export_state().get("active", {})})
    return 0


def _show_intent(ns: argparse.Namespace) -> int:
    _pj({"intent_id": ns.intent_id, "note": "intents stored with orders"})
    return 0


def _genealogy(ns: argparse.Namespace) -> int:
    from trading_ai.phase_institutional.oms import order_genealogy

    _pj(order_genealogy.get_tree(ns.intent_id))
    return 0


def _exec_alpha() -> int:
    from trading_ai.phase_institutional.execution_intelligence import execution_alpha

    _pj(execution_alpha.aggregate_week({}))
    return 0


def _forensics(ns: argparse.Namespace) -> int:
    from trading_ai.phase_institutional.execution_intelligence import execution_forensics

    _pj(execution_forensics.build_forensics_report(ns.incident_id, "test", [], "tbd", [], [], False))
    return 0


def _temporal() -> int:
    from trading_ai.phase_institutional.execution_intelligence import temporal_concentration_engine

    _pj(temporal_concentration_engine.analyze_positions([]))
    return 0


def _batch() -> int:
    _pj({"batch": "use portfolio_execution_coordinator"})
    return 0


def _netting() -> int:
    from trading_ai.phase_institutional.coordination import exposure_netting_engine

    _pj(exposure_netting_engine.net_batch([]))
    return 0


def _arb() -> int:
    _pj({"queue": []})
    return 0


def _models() -> int:
    from trading_ai.phase_institutional.model_risk import model_inventory

    _pj(model_inventory.summarize_inventory())
    return 0


def _unchallenged() -> int:
    from trading_ai.phase_institutional.model_risk import model_inventory

    _pj({"unchallenged": model_inventory.flag_inertia_models()})
    return 0


def _challenge(ns: argparse.Namespace) -> int:
    from trading_ai.phase_institutional.model_risk import model_challenge_program

    _pj(model_challenge_program.run_challenge(ns.model_id, ["a1"], []))
    return 0


def _assumptions() -> int:
    from trading_ai.phase_institutional.research import external_assumption_registry

    _pj(
        external_assumption_registry.evaluate_assumption(
            "a1",
            internal_basis="empirical",
            external_consensus="50-55%",
            divergence_justified=True,
            justification="prediction markets differ",
        )
    )
    return 0


def _research() -> int:
    _pj({"stages": "registered"})
    return 0


def _fdr() -> int:
    from trading_ai.phase_institutional.research import false_discovery_control

    _pj(false_discovery_control.assess_experiment("e1", 40, out_of_sample_validated=True, replication_verified=True))
    return 0


def _hyp() -> int:
    _pj({"hypotheses": []})
    return 0


def _frontier() -> int:
    from trading_ai.phase_institutional.portfolio_science import capital_efficiency_frontier

    _pj(
        capital_efficiency_frontier.compute_frontier(
            [0.2, 0.1],
            [0.2, 0.2],
            [[1.0, 0.0], [0.0, 1.0]],
            {"strat-001": 0.5, "strat-002": 0.5},
        )
    )
    return 0


def _fragility() -> int:
    from trading_ai.phase_institutional.portfolio_science import portfolio_fragility_model

    _pj(
        portfolio_fragility_model.score_fragility(
            1.2,
            {
                "strat-003_minus_20pct": {"sharpe_delta": -0.41, "drawdown_delta": 0.06},
                "strat-003_plus_20pct": {"sharpe_delta": 0.12, "drawdown_delta": -0.01},
            },
        )
    )
    return 0


def _crowd() -> int:
    from trading_ai.phase_institutional.alpha import signal_crowding_early_warning

    _pj(
        signal_crowding_early_warning.evaluate_signal(
            "news_sentiment_political",
            spread_compression_trend="stable",
            fill_time_trend="stable",
            timing_edge_erosion="none",
            opportunity_frequency_trend="stable",
        )
    )
    return 0


def _forecast() -> int:
    from trading_ai.phase_institutional.alpha import alpha_decay_forecaster

    _pj(alpha_decay_forecaster.project("strat-001", 0.071, 0.002))
    return 0


def _halflife() -> int:
    from trading_ai.phase_institutional.alpha import signal_half_life_model

    _pj({"half_life_weeks": signal_half_life_model.half_life_weeks(0.04)})
    return 0


def _adv(ns: argparse.Namespace) -> int:
    from trading_ai.phase_institutional.adversarial import adversarial_strategy_reviewer

    _pj(
        adversarial_strategy_reviewer.review_strategy(
            ns.strategy_id,
            regime_concentration=0.9,
            best_trade_removal_impact=0.1,
            evidence={"trade_ids": ["t1"], "truth_type": "HARD_FACT", "source_file": "x", "source_key": "k", "value": 1},
        )
    )
    return 0


def _gc() -> int:
    from trading_ai.phase_institutional.adversarial import governance_capture_detector

    _pj(
        governance_capture_detector.evaluate_governance_capture(
            council_approval_rate_4week=0.75,
            adversarial_implementation_rate=0.35,
            disagreement_filing_rate=0.12,
        )
    )
    return 0


def _risk_comm() -> int:
    from trading_ai.phase_institutional.governance import risk_committee_engine

    _pj(risk_committee_engine.convene_session("s1", decisions_reviewed=14, upheld=12, flagged=2))
    return 0


def _pms() -> int:
    from trading_ai.phase_institutional.governance import post_mortem_protocol

    _pj({"open": post_mortem_protocol.list_open_post_mortems()})
    return 0


def _playbooks() -> int:
    from trading_ai.phase_institutional.governance import regime_transition_playbook

    _pj(regime_transition_playbook.load_playbook("neutral_to_volatile"))
    return 0


def _reserve() -> int:
    from trading_ai.phase_institutional.capital import liquidity_stress_reserve

    _pj(liquidity_stress_reserve.reserve_state())
    return 0


def _meta_attr() -> int:
    from trading_ai.phase_institutional.meta import meta_attribution

    _pj(
        meta_attribution.attribute(
            0.071,
            {
                "phase2_signal_alpha": 0.048,
                "phase3_execution_alpha": 0.007,
                "phase4_engine_alpha": 0.006,
                "phase5_strategy_alpha": 0.004,
                "phase6_ecosystem_alpha": 0.002,
                "phase_extra_cross_market_alpha": 0.004,
                "execution_intelligence_alpha": 0.003,
            },
        )
    )
    return 0


def _human() -> int:
    from trading_ai.phase_institutional.human import human_decision_alpha

    _pj(
        human_decision_alpha.aggregate_overrides(
            [
                {"domain": "regime_assessment", "outcome_delta": 0.01},
                {"domain": "position_sizing", "outcome_delta": -0.02},
            ]
        )
    )
    return 0


def _complexity() -> int:
    from trading_ai.phase_institutional.meta import complexity_justification_engine

    _pj(complexity_justification_engine.evaluate_history({"layer_a": [-0.01, -0.02, -0.01]}))
    return 0


def _scenario(ns: argparse.Namespace) -> int:
    from trading_ai.phase_institutional.scenario import structural_scenario_lab

    _pj(structural_scenario_lab.run_compound_scenario(ns.scenario_id))
    return 0


def _status() -> int:
    from trading_ai.phase_institutional.shared import institutional_status

    _pj(institutional_status.build_institutional_status())
    return 0


def _validate() -> int:
    from trading_ai.phase_institutional import bootstrap

    _pj(bootstrap.validate_institutional_bootstrap())
    return 0


def _gap() -> int:
    from trading_ai.phase_institutional.shared import institutional_gap_check

    _pj(institutional_gap_check.run_institutional_gap_check())
    return 0


def _activation_seed() -> int:
    from trading_ai.ops.activation_control import run_activation_seed

    _pj(run_activation_seed())
    return 0


def _activation_flow() -> int:
    from trading_ai.ops.activation_control import run_activation_flow

    _pj(run_activation_flow())
    return 0


def _final_readiness_audit() -> int:
    from trading_ai.ops.activation_control import run_final_readiness_audit

    _pj(run_final_readiness_audit())
    return 0


def _smoke_readiness() -> int:
    from trading_ai.ops.activation_control import run_smoke_readiness

    _pj(run_smoke_readiness())
    return 0


def _controlled_backend_test() -> int:
    from trading_ai.ops.controlled_backend_test import run_controlled_backend_test

    out = run_controlled_backend_test()
    _pj(out)
    return 0 if out.get("ok") else 1
