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
    # "\r" (not "\n") before it: an in-place status line just written stays a status line, not a log line
    print("\r@@" + json.dumps(msg), flush=True)


def dur(sec: float) -> str:
    """Human duration: 45s, 5m 12s, 1h 06m."""
    sec = int(max(0, sec))
    if sec >= 3600:
        return f"{sec // 3600}h {sec % 3600 // 60:02d}m"
    return f"{sec // 60}m {sec % 60:02d}s" if sec >= 60 else f"{sec}s"


def clock(day_t: float) -> str:
    """Seconds since midnight -> 4:15 PM."""
    h, m = int(day_t // 3600) % 24, int(day_t % 3600 // 60)
    return f"{h % 12 or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"
