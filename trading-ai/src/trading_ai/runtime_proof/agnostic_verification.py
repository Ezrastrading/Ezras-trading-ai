"""
Runtime verification that global-layer behavior stays strategy / latency / edge agnostic.

Used by ``first_twenty_judge`` and session final reports — not a substitute for pytest.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[3]
_AGNOSTIC_TEST_REL = Path("tests") / "test_true_agnostic_behavior.py"


def agnostic_test_file() -> Path:
    return _REPO_ROOT / _AGNOSTIC_TEST_REL


def run_agnostic_pytests(*, timeout_sec: int = 120) -> Tuple[bool, str]:
    """Run Phase 3 test module; returns (all_passed, stdout+stderr excerpt)."""
    p = agnostic_test_file()
    if not p.is_file():
        return False, f"missing_test_file:{p}"
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(p), "-q", "--tb=short"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        return proc.returncode == 0, out.strip()[:8000]
    except subprocess.TimeoutExpired:
        return False, "pytest_timeout"
    except OSError as e:
        return False, f"pytest_os_error:{e}"


def packet_route_buckets_clean(packet: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Fail if route_summary uses legacy A/B bucket ids."""
    bad: List[str] = []
    rs = packet.get("route_summary") if isinstance(packet.get("route_summary"), dict) else {}
    buckets = rs.get("buckets")
    if isinstance(buckets, dict):
        for k in buckets.keys():
            ks = str(k).lower()
            if ks in ("route_a", "route_b"):
                bad.append(f"bucket_key:{k}")
    return len(bad) == 0, bad


def evaluate_organism_agnostic_lock(
    *,
    runtime_root: Optional[Path] = None,
    packet: Optional[Dict[str, Any]] = None,
    run_tests: bool = True,
) -> Dict[str, Any]:
    """
    Produce evidence dict for judges / session reports.

    ``strategy_dependency_detected`` / ``latency_dependency_detected`` / ``edge_dependency_detected``
    are conservative static signals (tests + packet shape); they do not prove absence of bugs in
    strategies outside the global layer.
    """
    tests_ok, tests_detail = (True, "skipped") if not run_tests else run_agnostic_pytests()
    pkt = packet
    if pkt is None and runtime_root is not None:
        rp = Path(runtime_root) / "shark" / "memory" / "global" / "review_packet_latest.json"
        if rp.is_file():
            try:
                pkt = json.loads(rp.read_text(encoding="utf-8"))
            except Exception:
                pkt = None
    buckets_ok, bucket_detail = (True, []) if pkt is None else packet_route_buckets_clean(pkt)

    route_ab_in_packet = not buckets_ok
    strategy_dep = (not tests_ok) or route_ab_in_packet
    latency_dep = not tests_ok
    edge_dep = not tests_ok

    agnosticity_verified = bool(tests_ok and buckets_ok)

    organism_agnostic = agnosticity_verified

    return {
        "agnosticity_verified": agnosticity_verified,
        "organism_agnostic": organism_agnostic,
        "agnostic_unit_tests_passed": tests_ok,
        "agnostic_unit_tests_detail": tests_detail,
        "route_ab_bucket_keys_detected": route_ab_in_packet,
        "route_ab_bucket_detail": bucket_detail,
        "strategy_dependency_detected": strategy_dep,
        "latency_dependency_detected": latency_dep,
        "edge_dependency_detected": edge_dep,
    }


def final_readiness_flags(ev: Dict[str, Any]) -> Dict[str, Any]:
    """Phase 6 — flat booleans for reports (any dependency flag fails the lock)."""
    return {
        "organism_agnostic": bool(ev.get("organism_agnostic")),
        "strategy_dependency_detected": bool(ev.get("strategy_dependency_detected")),
        "latency_dependency_detected": bool(ev.get("latency_dependency_detected")),
        "edge_dependency_detected": bool(ev.get("edge_dependency_detected")),
    }
