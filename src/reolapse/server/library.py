"""What's on disk: which camera-days exist and how far each has been processed."""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import date
from pathlib import Path

from .. import config
from ..config import DayPaths, slug

DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_cache: dict[tuple[str, str], tuple[float, dict]] = {}
CACHE_SECONDS = 15.0


def _day_dirs(camera: str) -> set[str]:
    days: set[str] = set()
    for root in {config.DATA_ROOT, config.WORK_ROOT}:
        cam_dir = root / slug(camera)
        if cam_dir.is_dir():
            days |= {p.name for p in cam_dir.iterdir() if p.is_dir() and DAY_RE.fullmatch(p.name)}
    return days


def _outputs(paths: DayPaths) -> list[dict]:
    seen: dict[str, dict] = {}
    for where, folder in (("local", paths.out), ("share", paths.published)):
        if folder.is_dir():
            for f in sorted(folder.iterdir()):  # no per-file stat: each is a round trip on a busy share
                if f.suffix.lower() in (".mp4", ".jpg"):
                    seen[f.name] = {"name": f.name, "where": where}
    return sorted(seen.values(), key=lambda o: o["name"])


_refreshing: set[tuple[str, str]] = set()
_lock = threading.Lock()


def camera_day(camera: str, day: str, fresh: bool = False) -> dict:
    """Status and summary numbers for one camera-day.

    Stale-while-revalidate: a cached answer is returned at once and refreshed in the background
    when older than CACHE_SECONDS, so the app stays responsive while a job saturates the share.
    """
    key = (camera, day)
    hit = _cache.get(key)
    if hit and not fresh:
        if time.monotonic() - hit[0] >= CACHE_SECONDS:
            with _lock:
                start = key not in _refreshing
                _refreshing.add(key)
            if start:
                threading.Thread(target=_refresh, args=(camera, day), daemon=True).start()
        return hit[1]
    return _scan(camera, day)


def _refresh(camera: str, day: str) -> None:
    try:
        _scan(camera, day)
    finally:
        with _lock:
            _refreshing.discard((camera, day))


def _scan(camera: str, day: str) -> dict:
    key = (camera, day)
    paths = DayPaths(camera, date.fromisoformat(day))
    recordings = json.loads(paths.files_index.read_text()) if paths.files_index.exists() else []
    counts = {"person": 0, "vehicle": 0, "animal": 0}
    hourly = [0] * 24
    hourly_by = {k: [0] * 24 for k in counts}  # objects starting in each hour, per type
    if paths.tubes.is_dir():
        for p in paths.tubes.glob("*.pkl"):
            t0, cat = (p.stem.split("_") + [""])[:2]  # 036000.0_person[_2]
            try:
                hour = min(23, int(float(t0) // 3600))
            except ValueError:
                continue
            hourly[hour] += 1
            hourly_by.setdefault(cat, [0] * 24)[hour] += 1
            counts[cat] = counts.get(cat, 0) + 1
    outputs = _outputs(paths)
    analyzed = sum(counts.values()) > 0
    if any(o["name"].endswith(".mp4") for o in outputs):
        status = "rendered"
    elif analyzed:
        status = "analyzed"
    elif recordings:
        status = "fetched"
    else:
        status = "empty"
    info = {
        "camera": camera, "day": day, "status": status,
        "recordings": len(recordings),
        "backgrounds": paths.backgrounds.is_dir() and any(paths.backgrounds.glob("*.jpg")),
        "counts": counts, "hourly": hourly, "hourly_by": hourly_by, "outputs": outputs,
    }
    _cache[key] = (time.monotonic(), info)
    return info


_day_list: dict[tuple[str, ...], tuple[float, list[str]]] = {}


def _list_days(cameras: list[str]) -> list[str]:
    found = sorted(set().union(*(_day_dirs(c) for c in cameras)) if cameras else set(), reverse=True)
    _day_list[tuple(cameras)] = (time.monotonic(), found)
    return found


def days(cameras: list[str]) -> list[dict]:
    """Every day any configured camera has data for, newest first (directory listing cached the same way)."""
    hit = _day_list.get(tuple(cameras))
    if hit is None:
        all_days = _list_days(cameras)
    else:
        all_days = hit[1]
        if time.monotonic() - hit[0] >= CACHE_SECONDS:
            threading.Thread(target=_list_days, args=(cameras,), daemon=True).start()
    return [{"day": d, "cameras": [camera_day(c, d) for c in cameras]} for d in all_days]


def output_file(camera: str, day: str, name: str) -> Path | None:
    """Path of a rendered output by file name, preferring the local copy."""
    if "/" in name or "\\" in name or name.startswith("."):
        return None
    paths = DayPaths(camera, date.fromisoformat(day))
    for folder in (paths.out, paths.published):
        f = folder / name
        if f.is_file():
            return f
    return None


def background_file(camera: str, day: str, slot: int) -> Path | None:
    f = DayPaths(camera, date.fromisoformat(day)).backgrounds / f"{slot:03d}.jpg"
    return f if f.is_file() else None
