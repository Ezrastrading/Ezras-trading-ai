"""Hard caps — aligned with capital_phase.detect_phase."""

from __future__ import annotations

from dataclasses import dataclass

from trading_ai.shark.capital_phase import detect_phase, phase_params


@dataclass(frozen=True)
class HardCaps:
    max_fraction_of_capital: float
    max_gap_total_fraction: float = 0.60


def default_caps_for_capital(capital: float) -> HardCaps:
    phase = detect_phase(capital)
    pp = phase_params(phase)
    return HardCaps(max_fraction_of_capital=pp.max_single_position_fraction, max_gap_total_fraction=0.60)


def clamp_to_hard_cap(desired_fraction: float, cap: HardCaps) -> float:
    return min(desired_fraction, cap.max_fraction_of_capital)
