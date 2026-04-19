"""Global pause in system_health vs avenue_pause map."""

from __future__ import annotations

import json


def test_avenue_pause_does_not_imply_other_avenue(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.nte.paths import nte_system_health_path

    p = nte_system_health_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {
                "global_pause": False,
                "avenue_pause": {"coinbase": True, "kalshi": False},
            }
        )
    )
    data = json.loads(p.read_text())
    assert data["avenue_pause"]["kalshi"] is False
