"""Avenue × gate registry for daemon verification — isolated per route; no cross-avenue proof."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Tuple

from trading_ai.multi_avenue.avenue_registry import merged_avenue_definitions


@dataclass(frozen=True)
class GateBinding:
    gate_id: str
    """If True, fake/replay harness may exercise full daemon contract for this pair."""
    gate_contract_wired_for_harness: bool
    """Production live orders / daemon — honest wiring (independent of fake tests)."""
    live_execution_wired: bool
    not_wired_reason: str


@dataclass(frozen=True)
class AvenueBinding:
    avenue_id: str
    avenue_name: str
    display_name: str
    venue_name: str
    wiring_status: str
    gates: Tuple[GateBinding, ...]


def _gate_bindings_for_avenue(row: Dict[str, Any]) -> Tuple[GateBinding, ...]:
    aid = str(row.get("avenue_id") or "").strip()
    gates_raw: List[str] = list(row.get("gates") or [])
    ws = str(row.get("wiring_status") or "")
    venue = str(row.get("venue_name") or "")

    if not gates_raw:
        return (
            GateBinding(
                gate_id="no_gate_registered",
                gate_contract_wired_for_harness=False,
                live_execution_wired=False,
                not_wired_reason="no_gates_registered_for_avenue_scaffold_or_empty",
            ),
        )

    out: List[GateBinding] = []
    for g in gates_raw:
        gid = str(g).strip()
        live = False
        harness = False
        reason = ""

        if aid == "A" and gid == "gate_a":
            harness = True
            live = True  # NTE path exists — still subject to switch/proof artifacts at runtime
            reason = ""
        elif aid == "A" and gid == "gate_b":
            # Scan/tick path — not the Gate A round-trip daemon contract
            harness = True
            live = False
            reason = "avenue_a_gate_b_is_scan_tick_path_not_gate_a_round_trip_daemon"
        elif aid == "B" and gid == "gate_b":
            harness = True
            live = False
            reason = "kalshi_independent_live_proof_and_daemon_cycle_not_universally_wired"
        elif aid == "C":
            harness = False
            live = False
            reason = "avenue_c_scaffold_execution_not_wired"
        else:
            harness = ws == "wired"
            live = ws == "wired" and aid not in ("C",)
            reason = "" if live else f"wiring_status_{ws}"

        out.append(
            GateBinding(
                gate_id=gid,
                gate_contract_wired_for_harness=harness,
                live_execution_wired=live,
                not_wired_reason=reason,
            )
        )
    return tuple(out)


def _expand_daemon_gates(row: Dict[str, Any]) -> Dict[str, Any]:
    """Include Gate B under A for matrix coverage (tick/scan path is distinct from Gate A round-trip)."""
    r = dict(row)
    if str(r.get("avenue_id") or "").strip() == "A":
        g = list(r.get("gates") or [])
        if "gate_b" not in g:
            g.append("gate_b")
        r["gates"] = g
    return r


def load_daemon_avenue_bindings(*, runtime_root: Optional[Any] = None) -> Tuple[AvenueBinding, ...]:
    rows = [_expand_daemon_gates(dict(x)) for x in merged_avenue_definitions(runtime_root=runtime_root)]
    result: List[AvenueBinding] = []
    for row in rows:
        aid = str(row.get("avenue_id") or "")
        gb = _gate_bindings_for_avenue(row)
        result.append(
            AvenueBinding(
                avenue_id=aid,
                avenue_name=str(row.get("avenue_name") or ""),
                display_name=str(row.get("display_name") or ""),
                venue_name=str(row.get("venue_name") or ""),
                wiring_status=str(row.get("wiring_status") or ""),
                gates=gb,
            )
        )
    return tuple(result)


def iter_avenue_gate_pairs(*, runtime_root: Optional[Any] = None) -> Iterator[Tuple[AvenueBinding, GateBinding]]:
    for av in load_daemon_avenue_bindings(runtime_root=runtime_root):
        for g in av.gates:
            yield av, g


def registry_summary_dict(*, runtime_root: Optional[Any] = None) -> Dict[str, Any]:
    return {
        "truth_version": "daemon_avenue_gate_registry_v1",
        "avenues": [
            {
                "avenue_id": a.avenue_id,
                "avenue_name": a.avenue_name,
                "display_name": a.display_name,
                "venue_name": a.venue_name,
                "wiring_status": a.wiring_status,
                "gates": [
                    {
                        "gate_id": g.gate_id,
                        "gate_contract_wired_for_harness": g.gate_contract_wired_for_harness,
                        "live_execution_wired": g.live_execution_wired,
                        "not_wired_reason": g.not_wired_reason,
                    }
                    for g in a.gates
                ],
            }
            for a in load_daemon_avenue_bindings(runtime_root=runtime_root)
        ],
        "honesty": (
            "live_execution_wired is production intent for this repo revision — "
            "independent proof artifacts may still block switch_live. "
            "No avenue inherits another avenue's proof."
        ),
    }
