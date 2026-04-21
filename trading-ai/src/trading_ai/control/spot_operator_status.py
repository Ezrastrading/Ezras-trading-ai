"""Human-readable spot inventory lines for ``data/control`` operator files."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Mapping, Optional

from trading_ai.control.paths import control_data_dir, live_status_path
from trading_ai.reality.paths import reality_data_dir


def reality_snapshot_path() -> Path:
    return reality_data_dir() / "reality_snapshot.txt"


def write_spot_operator_snapshots(
    snap: Mapping[str, Any],
    *,
    reconciliation: Optional[Mapping[str, Any]] = None,
    append: bool = False,
) -> Dict[str, str]:
    """
    Write ``live_status.txt`` (under control) and ``reality_snapshot.txt`` (under reality).

    Safe to call after micro-validation or manual diagnostics.
    """
    control_data_dir()
    reality_data_dir()
    ts = datetime.now(timezone.utc).isoformat()
    lines = [
        f"generated_at_utc: {ts}",
        f"product_id: {snap.get('product_id')}",
        f"Coinbase {snap.get('validation_base_asset')} inventory (exchange): {snap.get('exchange_base_qty')}",
    ]
    mv = snap.get("base_inventory_market_value_usd")
    if mv is not None:
        lines.append(f"  ~ USD mark value (base only): ${mv:.4f}")
    lines.append(f"Coinbase USD available: ${snap.get('quote_available_usd')}")
    lines.append(f"Coinbase USDC available: ${snap.get('quote_available_usdc')}")
    lines.append(f"Quote combined (USD+USDC): ${snap.get('quote_available_combined_usd')}")
    te = snap.get("total_spot_equity_usd")
    if te is not None:
        lines.append(f"Total spot equity considered (quote + base mark): ${te:.4f}")
    lines.append(f"Internal {snap.get('validation_base_asset')} tracked (open positions): {snap.get('internal_base_qty')}")
    lines.append(f"snapshot_source: {snap.get('source')}")

    if reconciliation:
        lines.append(
            f"Reconciliation mode: {reconciliation.get('reconciliation_mode_used')}"
        )
        lines.append(f"reconciliation_ok: {reconciliation.get('reconciliation_ok')}")
        rn = reconciliation.get("reconciliation_notes") or []
        if rn:
            lines.append("reconciliation_notes:")
            for n in rn[:6]:
                lines.append(f"  - {n}")

    text = "\n".join(lines) + "\n"
    out_map = {
        "live_status.txt": live_status_path(),
        "reality_snapshot.txt": reality_snapshot_path(),
    }
    mode = "a" if append else "w"
    written: Dict[str, str] = {}
    for label, path in out_map.items():
        with path.open(mode, encoding="utf-8") as f:
            if append:
                f.write("\n--- spot_inventory ---\n")
            f.write(text)
        written[label] = str(path)
    return written
