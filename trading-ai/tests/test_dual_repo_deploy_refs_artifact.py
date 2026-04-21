from __future__ import annotations

import json
from pathlib import Path


def test_deployed_refs_artifact_schema(tmp_path: Path) -> None:
    out = tmp_path / "deployed_refs.json"
    payload = {
        "truth_version": "deployed_refs_v1",
        "generated_at_utc": "2026-01-01T00:00:00Z",
        "public": {"path": "/opt/ezra-public", "ref": "main", "sha": "abc"},
        "private": {"path": "/opt/ezra-private", "ref": "main", "sha": "def"},
    }
    out.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["truth_version"] == "deployed_refs_v1"
    assert set(loaded.keys()) == {"truth_version", "generated_at_utc", "public", "private"}
    assert set(loaded["public"].keys()) == {"path", "ref", "sha"}
    assert set(loaded["private"].keys()) == {"path", "ref", "sha"}

