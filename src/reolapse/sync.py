"""Sharing analysis results between Macs.

Analyses write objects (tubes/) and backgrounds (bg/) to this Mac's working folder, which is fast,
then copy them to the share so another Mac using the same archive sees the day too (DayPaths reads
whichever copy is newer). The copy lands in a staging folder first and is swapped in, so a reader
never sees a half-copied day.
"""

from __future__ import annotations

import shutil
import time
from datetime import date
from pathlib import Path

from . import config
from .config import DayPaths, slug

SKIP = shutil.ignore_patterns("_thumbs", "checkpoint.json*", "*.tmp")


def _copy_folder(src: Path, dest: Path) -> int:
    staging = dest.with_name(dest.name + ".incoming")
    old = dest.with_name(dest.name + ".old")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(src, staging, ignore=SKIP)
    if dest.exists():
        shutil.rmtree(old, ignore_errors=True)
        dest.rename(old)
    staging.rename(dest)
    shutil.rmtree(old, ignore_errors=True)
    return sum(f.stat().st_size for f in dest.rglob("*") if f.is_file())


def needs_sharing(paths: DayPaths) -> bool:
    """Does this Mac hold a newer analysis of the day than the share?"""
    if paths.work.resolve() == paths.root.resolve():
        return False
    local, shared = paths.work / "tubes", paths.root / "tubes"
    return local.is_dir() and paths._stamp(local, "tubes") > 0 \
        and paths._stamp(local, "tubes") > paths._stamp(shared, "tubes") + 1 \
        and not (local / "checkpoint.json").exists()


def share_results(paths: DayPaths, log=print) -> int:
    """Copy the day's objects and backgrounds to the share; returns bytes copied."""
    if paths.work.resolve() == paths.root.resolve():
        return 0
    t0 = time.monotonic()
    log("   copying the objects and backgrounds to the share, so other Macs see this day too…")
    total = 0
    paths.root.mkdir(parents=True, exist_ok=True)
    for sub in ("tubes", "bg"):
        src = paths.work / sub
        if src.is_dir():
            total += _copy_folder(src, paths.root / sub)
    log(f"   shared {total / 1e6:.0f} MB in {time.monotonic() - t0:.0f}s")
    return total


def pending() -> list[DayPaths]:
    """Camera-days this Mac analyzed that the share doesn't have yet (or has an older copy of)."""
    from .settings import load

    out = []
    if config.WORK_ROOT.resolve() == config.DATA_ROOT.resolve():
        return out
    for camera in load()["cameras"]:
        d = config.WORK_ROOT / slug(camera)
        if not d.is_dir():
            continue
        for day_dir in sorted(d.iterdir()):
            try:
                paths = DayPaths(camera, date.fromisoformat(day_dir.name))
            except ValueError:
                continue
            if needs_sharing(paths):
                out.append(paths)
    return out
