from __future__ import annotations

import os
import tempfile
from pathlib import Path


def test_global_governance_dir_defaults_to_runtime_root() -> None:
    with tempfile.TemporaryDirectory(prefix="ezra_gov_test_") as td:
        root = Path(td).resolve()
        os.environ.pop("EZRAS_GOVERNANCE_DIR", None)
        os.environ["EZRAS_RUNTIME_ROOT"] = str(root)

        from trading_ai.global_layer._bot_paths import global_layer_governance_dir

        p = global_layer_governance_dir()
        assert str(p).startswith(str(root))
        assert (root / "data" / "governance" / "global_layer") == p

