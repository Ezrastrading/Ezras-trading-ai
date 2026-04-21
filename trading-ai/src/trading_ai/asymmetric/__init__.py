from __future__ import annotations

from typing import Any

_EXPORTS = {
    "asymmetric_gate_cycle": "trading_ai.asymmetric.asymmetric_gate_engine:asymmetric_gate_cycle",
    "record_asymmetric_trade_event": "trading_ai.asymmetric.asymmetric_tracker:record_asymmetric_trade_event",
    "record_asymmetric_trade_from_normalized_record": "trading_ai.asymmetric.asymmetric_tracker:record_asymmetric_trade_from_normalized_record",
    "load_asymmetric_config": "trading_ai.asymmetric.config:load_asymmetric_config",
}

__all__ = [
    "asymmetric_gate_cycle",
    "load_asymmetric_config",
    "record_asymmetric_trade_event",
    "record_asymmetric_trade_from_normalized_record",
]


def __getattr__(name: str) -> Any:
    """
    Lazy exports to avoid import-time side effects / circular imports.
    """
    spec = _EXPORTS.get(name)
    if not spec:
        raise AttributeError(name)
    mod, sym = spec.split(":")
    import importlib

    m = importlib.import_module(mod)
    return getattr(m, sym)

