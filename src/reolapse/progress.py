"""Machine-readable progress for the app: `@@{json}` lines when REOLAPSE_PROGRESS=json (set by the job queue)."""

from __future__ import annotations

import json
import os
import time

_ENABLED = os.environ.get("REOLAPSE_PROGRESS") == "json"
_last = 0.0


def emit(stage: str, frac: float | None = None, camera: str | None = None, detail: str | None = None,
         force: bool = False) -> None:
    """Report progress; throttled to ~2 per second unless `force`."""
    global _last
    if not _ENABLED:
        return
    now = time.monotonic()
    if not force and now - _last < 0.5:
        return
    _last = now
    msg = {"stage": stage, "camera": camera, "detail": detail}
    if frac is not None:
        msg["frac"] = round(max(0.0, min(1.0, frac)), 4)
    print("\n@@" + json.dumps(msg), flush=True)
