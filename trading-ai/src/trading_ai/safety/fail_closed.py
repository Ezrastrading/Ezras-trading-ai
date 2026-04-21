from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class FailClosedError(RuntimeError):
    pass


def fail_closed(reason: str, *, action: str = "FAIL_CLOSED", severity: str = "CRITICAL", meta: Optional[Dict[str, Any]] = None) -> None:
    payload = {
        "severity": severity,
        "action": action,
        "reason": str(reason),
        "meta": meta or {},
    }
    logger.critical("FAIL_CLOSED %s", json.dumps(payload, default=str, sort_keys=True))
    raise FailClosedError(str(reason))

