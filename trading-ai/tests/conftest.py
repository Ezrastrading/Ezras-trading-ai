import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


@pytest.fixture(autouse=True)
def _ezras_dry_run_for_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid accidental live venue submits; tests that need live pass ``execute_live=True``."""
    monkeypatch.setenv("EZRAS_DRY_RUN", "true")
