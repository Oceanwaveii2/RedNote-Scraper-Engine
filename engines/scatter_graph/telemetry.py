"""Content-free local telemetry for Scatter API and worker diagnostics."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone

from paths import DATA


_lock = threading.Lock()
_log = DATA / "telemetry" / "scatter-events.jsonl"


def emit(event: str, **fields) -> None:
    """Record timing and state only; never prompts, source text or credentials."""
    record = {"at": datetime.now(timezone.utc).isoformat(), "event": event,
              "pid": os.getpid(), "build": os.environ.get("SCATTER_BUILD_ID", "dev"),
              **fields}
    try:
        _log.parent.mkdir(parents=True, exist_ok=True)
        with _lock, _log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    except OSError:
        # A failed telemetry write must not change an authored graph operation.
        pass
