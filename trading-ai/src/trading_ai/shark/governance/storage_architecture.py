"""Compatibility shim for imports like ``trading_ai.shark.governance.storage_architecture``.

Prefer ``trading_ai.governance.storage_architecture`` in new code.
"""

from trading_ai.governance.storage_architecture import (  # noqa: F401
    shark_data_dir,
    shark_state_path,
)

__all__ = ("shark_data_dir", "shark_state_path")
