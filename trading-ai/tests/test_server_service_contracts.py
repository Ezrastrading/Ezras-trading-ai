from __future__ import annotations

from pathlib import Path


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_systemd_units_contain_required_contract_fields() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    ops = repo_root / "docs" / "systemd" / "ezra-ops.service"
    research = repo_root / "docs" / "systemd" / "ezra-research.service"
    assert ops.is_file()
    assert research.is_file()

    ops_txt = _read(ops)
    research_txt = _read(research)

    for txt in (ops_txt, research_txt):
        assert "WorkingDirectory=/opt/ezra-public/trading-ai" in txt
        assert "Environment=EZRAS_RUNTIME_ROOT=/opt/ezra-runtime" in txt
        assert "Environment=PYTHONPATH=/opt/ezra-private/trading-ai/src:/opt/ezra-public/trading-ai/src" in txt
        assert "EnvironmentFile=/opt/ezra-runtime/env/common.env" in txt
        assert "After=network-online.target" in txt
        assert "WantedBy=multi-user.target" in txt
        assert "Restart=always" in txt

    assert "-/opt/ezra-runtime/env/ops.env" in ops_txt
    assert "--role ops" in ops_txt
    assert "-/opt/ezra-runtime/env/research.env" in research_txt
    assert "--role research" in research_txt

