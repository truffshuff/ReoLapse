"""Storage cleanup: what the retention settings allow to be deleted, and doing it.

Rules (days counted from today, by the day the footage is from):
- raw footage older than `raw_days`, once that day has been rendered (or is older than `results_days`
  anyway): unprocessed footage is never thrown away just for being old;
- footage of the other stream (Fluent files left after a day was downloaded again in Clear, or the
  reverse), once the current stream's download is complete;
- leftovers of interrupted downloads (.part, .remux.mp4) older than a day;
- everything for a day (videos, stills, objects, backgrounds, raw) older than `results_days`;
- this Mac's copies of finished videos and pictures (working folder) more than 2 days old, once the
  share has an identical copy (recent days stay local, where they play faster);
- the working folder's picture cache and set-aside folders older than 30 days.

Days with a queued or running job are never touched. `plan()` only looks; `execute()` deletes.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import config
from .config import DayPaths, slug

DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
CACHE_MAX_AGE = 30  # days, for _scaled and _set-aside in the working folder
LOCAL_COPY_DAYS = 2  # finished outputs stay in the working folder this long after their day

REASONS = {
    "raw": "Downloaded footage older than the raw-footage limit (day already rendered)",
    "superseded": "Footage of the other stream, replaced by a newer download",
    "leftover": "Leftovers of interrupted downloads",
    "results": "Days older than the results limit (videos, pictures, objects, footage)",
    "cache": "Old picture cache and set-aside folders",
    "localcopy": "This Mac's copies of finished videos already on the share",
}


@dataclass
class Item:
    path: Path
    bytes: int
    reason: str
    day: str = ""
    camera: str = ""


@dataclass
class Plan:
    items: list[Item] = field(default_factory=list)

    @property
    def bytes(self) -> int:
        return sum(i.bytes for i in self.items)

    def summary(self) -> list[dict]:
        out = {}
        for i in self.items:
            s = out.setdefault(i.reason, {"reason": i.reason, "label": REASONS[i.reason], "bytes": 0, "count": 0,
                                          "days": set()})
            s["bytes"] += i.bytes
            s["count"] += 1
            if i.day:
                s["days"].add(i.day)
        return [{**s, "days": sorted(s["days"])} for s in out.values()]


def size_of(p: Path) -> int:
    try:
        if p.is_file():
            return p.stat().st_size
        total = 0
        for root, _dirs, files in os.walk(p):
            for f in files:
                try:
                    total += os.stat(os.path.join(root, f)).st_size
                except OSError:
                    pass
        return total
    except OSError:
        return 0


def _age_days(day: str, today: date) -> int:
    return (today - date.fromisoformat(day)).days


def _rendered(paths: DayPaths) -> bool:
    return any(any(f.glob("synopsis_*.mp4")) for f in {paths.out, paths.published} if f.is_dir())


def _camera_dirs() -> dict[str, str]:
    """slug -> camera name, for the configured cameras and any camera folder found on disk."""
    from .settings import load

    names = {slug(c): c for c in load()["cameras"]}
    for root in {config.DATA_ROOT, config.WORK_ROOT}:
        if root.is_dir():
            for d in root.iterdir():
                if d.is_dir() and not d.name.startswith(("_", ".")) and d.name not in names:
                    names[d.name] = d.name
    return names


def plan(retention: dict, protected: set[tuple[str, str]] = frozenset(), today: date | None = None) -> Plan:
    """What may be deleted. `protected`: (camera slug, day) pairs with queued or running jobs."""
    today = today or date.today()
    raw_days, results_days = int(retention["raw_days"]), int(retention["results_days"])
    out = Plan()
    for cslug, camera in _camera_dirs().items():
        days = set()
        for root in {config.DATA_ROOT, config.WORK_ROOT}:
            d = root / cslug
            if d.is_dir():
                days |= {p.name for p in d.iterdir() if p.is_dir() and DAY_RE.fullmatch(p.name)}
        for day in sorted(days):
            if (cslug, day) in protected:
                continue
            age = _age_days(day, today)
            paths = DayPaths(camera, date.fromisoformat(day))
            roots = [r / cslug / day for r in {config.DATA_ROOT, config.WORK_ROOT} if (r / cslug / day).is_dir()]
            if age > results_days:
                for r in roots:
                    out.items.append(Item(r, size_of(r), "results", day, camera))
                continue
            if (age > LOCAL_COPY_DAYS and paths.out.is_dir() and paths.published.is_dir()
                    and paths.out.resolve() != paths.published.resolve()):
                for f in paths.out.iterdir():
                    twin = paths.published / f.name
                    try:
                        if f.is_file() and twin.is_file() and twin.stat().st_size == f.stat().st_size:
                            out.items.append(Item(f, f.stat().st_size, "localcopy", day, camera))
                    except OSError:
                        pass
            raw = paths.raw
            if not raw.is_dir():
                continue
            if age > raw_days and _rendered(paths):
                out.items.append(Item(raw, size_of(raw), "raw", day, camera))
                continue
            try:
                listed = json.loads(paths.files_index.read_text())
            except (OSError, ValueError):
                listed = []
            names = {f["path"] for f in listed}
            current = {Path(n).name.split("_")[0] for n in names}
            complete = bool(names) and all((raw / n).exists() for n in names)
            for f in raw.iterdir():
                if f.name.endswith((".part", ".remux.mp4")):
                    if time.time() - f.stat().st_mtime > 86400:
                        out.items.append(Item(f, f.stat().st_size, "leftover", day, camera))
                elif (f.suffix == ".mp4" and complete and current and f.name not in names
                      and f.name.split("_")[0] not in current):
                    out.items.append(Item(f, f.stat().st_size, "superseded", day, camera))
    for name in ("_scaled", "_set-aside"):
        d = config.WORK_ROOT / name
        if d.is_dir():
            for f in d.iterdir():
                try:
                    if time.time() - f.stat().st_mtime > CACHE_MAX_AGE * 86400:
                        out.items.append(Item(f, size_of(f), "cache"))
                except OSError:
                    pass
    return out


def execute(p: Plan, log=print) -> int:
    """Delete everything in the plan; returns the bytes freed."""
    freed = 0
    for i in p.items:
        try:
            if i.path.is_dir():
                shutil.rmtree(i.path)
            else:
                i.path.unlink(missing_ok=True)
            freed += i.bytes
        except OSError as e:
            log(f"   couldn't delete {i.path}: {e}")
    for s in p.summary():
        log(f"   {s['label']}: {s['count']} item(s), {s['bytes'] / 1e9:.1f} GB")
    return freed


def disk(path: Path) -> dict | None:
    try:
        u = shutil.disk_usage(path)
        return {"path": str(path), "total": u.total, "used": u.used, "free": u.free}
    except OSError:
        return None
