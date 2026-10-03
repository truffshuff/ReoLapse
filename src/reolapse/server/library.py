"""What's on disk: which camera-days exist and how far each has been processed."""

from __future__ import annotations

import json
import re
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


def camera_day(camera: str, day: str, fresh: bool = False) -> dict:
    """Status and summary numbers for one camera-day (cached briefly; the share is slow)."""
    key = (camera, day)
    hit = _cache.get(key)
    if hit and not fresh and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    paths = DayPaths(camera, date.fromisoformat(day))
    recordings = json.loads(paths.files_index.read_text()) if paths.files_index.exists() else []
    counts = {"person": 0, "vehicle": 0, "animal": 0}
    hourly = [0] * 24
    if paths.tubes.is_dir():
        for p in paths.tubes.glob("*.pkl"):
            t0, cat = (p.stem.split("_") + [""])[:2]  # 036000.0_person[_2]
            try:
                hourly[min(23, int(float(t0) // 3600))] += 1
            except ValueError:
                continue
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
        "counts": counts, "hourly": hourly, "outputs": outputs,
    }
    _cache[key] = (time.monotonic(), info)
    return info


def days(cameras: list[str]) -> list[dict]:
    """Every day any configured camera has data for, newest first."""
    all_days = sorted(set().union(*(_day_dirs(c) for c in cameras)) if cameras else set(), reverse=True)
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
