from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

from trading_ai.asymmetric.config import AsymmetricConfig, load_asymmetric_config


@dataclass(frozen=True)
class AsymmetricCapitalSplit:
    total_capital_usd: float
    core_capital_usd: float
    asymmetric_capital_usd: float
    core_capital_pct: float
    asymmetric_capital_pct: float
    enabled: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def compute_capital_split(
    *,
    total_capital_usd: float,
    cfg: Optional[AsymmetricConfig] = None,
) -> AsymmetricCapitalSplit:
    c = cfg or load_asymmetric_config()
    total = max(0.0, float(total_capital_usd))
    if not c.enabled or c.asym_capital_pct <= 0:
        return AsymmetricCapitalSplit(
            total_capital_usd=total,
            core_capital_usd=total,
            asymmetric_capital_usd=0.0,
            core_capital_pct=float(getattr(c, "core_capital_pct", 1.0)),
            asymmetric_capital_pct=0.0,
            enabled=False,
        )
    a = max(0.0, min(total, total * float(c.asym_capital_pct)))
    return AsymmetricCapitalSplit(
        total_capital_usd=total,
        core_capital_usd=float(total - a),
        asymmetric_capital_usd=float(a),
        core_capital_pct=float(getattr(c, "core_capital_pct", 1.0 - float(c.asym_capital_pct))),
        asymmetric_capital_pct=float(c.asym_capital_pct),
        enabled=True,
    )


def max_position_notional_usd(
    *,
    asymmetric_bucket_usd: float,
    cfg: Optional[AsymmetricConfig] = None,
) -> float:
    c = cfg or load_asymmetric_config()
    b = max(0.0, float(asymmetric_bucket_usd))
    return max(0.0, b * float(getattr(c, "asym_max_position_pct_of_asym_bucket", 0.0)))

