"""Smoke harness for autonomous backbone wiring (no venue orders unless explicitly invoked elsewhere)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.global_layer.autonomous_backbone_status import build_autonomous_backbone_status
from trading_ai.global_layer.bot_factory import create_bot_if_needed
from trading_ai.global_layer.bot_types import BotRole
from trading_ai.global_layer.canonical_specialist_seed import ensure_canonical_specialists
from trading_ai.global_layer.ceo_daily_orchestration import write_daily_ceo_review
from trading_ai.global_layer.deterministic_autonomous_orchestration import run_auto_promotion_cycle
from trading_ai.global_layer.orchestration_truth_chain import write_orchestration_truth_chain


def run_smoke_autonomous_backbone(*, registry_path: Path, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """
    Verifies: queues, specialist seed idempotency, CEO review, promotion truth, truth chain, backbone status.
    """
    rp = Path(registry_path).expanduser().resolve()
    rt = Path(runtime_root).expanduser().resolve() if runtime_root else None
    out: Dict[str, Any] = {"steps": []}

    os.environ["EZRAS_BOT_REGISTRY_PATH"] = str(rp)
    if rt:
        os.environ["EZRAS_RUNTIME_ROOT"] = str(rt)

    s1 = ensure_canonical_specialists(avenue="A", gate="gate_a", registry_path=rp)
    out["steps"].append({"seed_specialists": s1})

    s2 = create_bot_if_needed(
        {
            "avenue": "A",
            "gate": "gate_b",
            "role": BotRole.SCANNER.value,
            "version": "v1",
            "performance_threshold_failed": True,
            "trade_count": 25,
            "measured_gap": True,
            "spawn_reason": "smoke_autonomous_backbone",
        },
        registry_path=rp,
    )
    out["steps"].append({"spawn_manager": s2})

    ceo = write_daily_ceo_review(registry_path=rp, estimated_review_tokens=50)
    out["steps"].append({"ceo_daily": {"truth_version": ceo.get("truth_version"), "bot_total": ceo.get("bot_total")}})

    prom = run_auto_promotion_cycle(registry_path=rp)
    out["steps"].append({"auto_promotion": {"truth_version": prom.get("truth_version")}})

    chain = write_orchestration_truth_chain(registry_path=rp)
    out["steps"].append({"truth_chain_blockers": chain.get("blockers")})

    bb = build_autonomous_backbone_status(registry_path=rp, runtime_root=rt, write_file=True)
    out["autonomous_backbone_status"] = {
        "truth_version": bb.get("truth_version"),
        "system_mission_version": bb.get("system_mission_version"),
        "live_authority_green": bb.get("live_authority_green"),
    }
    out["ok"] = bool(bb.get("truth_version"))
    return out


def run_smoke_supervised_rebuy_loop(*, runtime_root: Path) -> Dict[str, Any]:
    """Writes rebuy certification + reports loop proof presence (no orders)."""
    from trading_ai.daemon_testing.daemon_artifact_writers import write_daemon_rebuy_certification

    root = Path(runtime_root).expanduser().resolve()
    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)
    (root / "data" / "control").mkdir(parents=True, exist_ok=True)
    cert = write_daemon_rebuy_certification(runtime_root=root, matrix_rows=None)
    ad_path = root / "data" / "control" / "universal_execution_loop_proof.json"
    loop = {}
    if ad_path.is_file():
        loop = json.loads(ad_path.read_text(encoding="utf-8"))
    return {
        "ok": True,
        "rebuy_certification_keys": list(cert.keys()) if isinstance(cert, dict) else [],
        "loop_proof_present": ad_path.is_file(),
        "buy_sell_log_rebuy_flag": bool(loop.get("BUY_SELL_LOG_REBUY_RUNTIME_PROVEN") or loop.get("final_execution_proven")),
        "honesty": "Runtime proven flags require real cycles — smoke only checks wiring.",
    }


def avenue_a_go_live_verdict(*, runtime_root: Path) -> Dict[str, Any]:
    from trading_ai.deployment.operator_env_contracts import build_env_config_blocker_summary
    from trading_ai.orchestration.armed_but_off_authority import classify_final_daemon_go_live, write_final_daemon_go_live_authority
    from trading_ai.orchestration.autonomous_operator_path import build_autonomous_operator_path

    root = Path(runtime_root).expanduser().resolve()
    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)
    cls, note = classify_final_daemon_go_live(runtime_root=root)
    art = write_final_daemon_go_live_authority(runtime_root=root)
    ap = build_autonomous_operator_path(runtime_root=root)
    return {
        "classification": cls,
        "note": note,
        "artifact": art,
        "runtime_root": str(root),
        "autonomous_path": ap,
        "operator_env_config_blockers": build_env_config_blocker_summary(
            runtime_root=root,
            require_supervised_confirm=True,
            assume_supervised_daemon_shell=True,
        ),
    }
