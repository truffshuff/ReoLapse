"""Download a day of continuous recordings + the NVR's AI event list for each camera."""

from __future__ import annotations

import json
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from . import nvr as nvrmod
from .config import DayPaths
from .media import is_fragmented, remux

LOCAL_TMP = Path(tempfile.gettempdir()) / "reolapse"


def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day)
    return start, start + timedelta(days=1)


async def fetch_camera(nvr, camera: str, day: date, stream: str) -> None:
    paths = DayPaths(camera, day)
    ch = nvrmod.find_channel(nvr, camera)
    start, end = day_bounds(day)
    print(f"== {camera} (ch{ch}) {day} [{stream}] -> {paths.root}")

    # Events: one record per typed NVR event, kept raw; analyze merges them into windows.
    by_trig = await nvrmod.event_files(nvr, ch, start, end - timedelta(seconds=1), stream="sub")
    events = []
    for trig, files in by_trig.items():
        if trig in (nvrmod.VOD_trigger.NONE, nvrmod.VOD_trigger.TIMER):
            continue  # keep every real event type, incl. crossline / intrusion / linger rules
        for f in files:
            events.append({"type": trig.name.lower(), "start": f.start_time.isoformat(), "end": f.end_time.isoformat()})
    events.sort(key=lambda e: e["start"])
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.events.write_text(json.dumps(events, indent=1))
    counts = {}
    for e in events:
        counts[e["type"]] = counts.get(e["type"], 0) + 1
    print(f"  events: {counts}")

    # Recordings: search a little before midnight so the file spanning 00:00 is included.
    files = await nvrmod.recording_files(nvr, ch, start - timedelta(hours=1), end - timedelta(seconds=1), stream)
    files = [f for f in files if f.end_time.replace(tzinfo=None) > start and f.start_time.replace(tzinfo=None) < end]
    index = []
    for i, f in enumerate(files, 1):
        dest = paths.raw / f"{stream}_{f.start_time:%Y%m%d_%H%M%S}.mp4"
        index.append({"path": dest.name, "start": f.start_time.isoformat(), "end": f.end_time.isoformat(), "stream": stream})
        if dest.exists():
            if is_fragmented(dest):
                remux(dest, dest)
                print(f"  [{i}/{len(files)}] {dest.name} (have, remuxed)")
            else:
                print(f"  [{i}/{len(files)}] {dest.name} (have)")
            continue
        t0 = time.monotonic()

        def progress(n, i=i, name=dest.name):
            sys.stdout.write(f"\r  [{i}/{len(files)}] {name} {n / 1e6:7.0f} MB")
            sys.stdout.flush()

        # Download to local disk, then remux (lossless) onto the share as an indexed MP4.
        tmp = LOCAL_TMP / dest.name
        await nvrmod.download_file(nvr, ch, f, stream, tmp, progress)
        dest.parent.mkdir(parents=True, exist_ok=True)
        remux(tmp, dest)
        tmp.unlink()
        dt = time.monotonic() - t0
        size = dest.stat().st_size
        print(f"\r  [{i}/{len(files)}] {dest.name} {size / 1e6:7.0f} MB in {dt:4.0f}s ({size / 1e6 / dt:.1f} MB/s)")
    paths.files_index.write_text(json.dumps(index, indent=1))


async def run(cameras: list[str], day: date, stream: str) -> None:
    async with nvrmod.connect() as nvr:
        for cam in cameras:
            await fetch_camera(nvr, cam, day, stream)
