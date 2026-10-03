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
from .media import is_fragmented, remux, remux_local
from .progress import emit

LOCAL_TMP = Path(tempfile.gettempdir()) / "reolapse"


def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day)
    return start, start + timedelta(days=1)


def _size(n: float) -> str:
    return f"{n / 1e9:.2f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


def _dur(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600}h {sec % 3600 // 60:02d}m" if sec >= 3600 else f"{sec // 60}m {sec % 60:02d}s" if sec >= 60 else f"{sec}s"


def _status(text: str) -> None:
    """An in-place status line (carriage return): the app shows it live without filling the log."""
    sys.stdout.write("\r" + text)
    sys.stdout.flush()


def copy_with_progress(src: Path, dest: Path, label: str) -> None:
    """Copy to the share in chunks, reporting speed; writes .part then renames."""
    total = src.stat().st_size
    part = dest.with_name(dest.name + ".part")
    t0 = last = time.monotonic()
    done = 0
    with open(src, "rb") as fi, open(part, "wb") as fo:
        while chunk := fi.read(8 << 20):
            fo.write(chunk)
            done += len(chunk)
            now = time.monotonic()
            if now - last >= 1:
                last = now
                rate = done / (now - t0)
                _status(f"{label}: copying to the share {_size(done)} of {_size(total)} at {rate / 1e6:.1f} MB/s, "
                        f"{_dur((total - done) / max(rate, 1))} left")
    part.replace(dest)


async def fetch_camera(nvr, camera: str, day: date, stream: str) -> None:
    paths = DayPaths(camera, day)
    ch = nvrmod.find_channel(nvr, camera)
    start, end = day_bounds(day)
    stream_name = "Clear (main stream)" if stream == "main" else "Fluent (sub stream)"
    print(f"== {camera}: downloading {day} from NVR channel {ch}, {stream_name}")
    print(f"   saving to {paths.raw}")
    cam_t0 = time.monotonic()

    # Events: one record per typed NVR event, kept raw; analyze merges them into windows.
    print("   asking the NVR for the day's detection events…")
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
    counts: dict[str, int] = {}
    for e in events:
        counts[e["type"]] = counts.get(e["type"], 0) + 1
    print("   NVR events: " + (", ".join(f"{n} {k}" for k, n in sorted(counts.items(), key=lambda kv: -kv[1])) or "none"))

    # Recordings: search a little before midnight so the file spanning 00:00 is included.
    files = await nvrmod.recording_files(nvr, ch, start - timedelta(hours=1), end - timedelta(seconds=1), stream)
    files = [f for f in files if f.end_time.replace(tzinfo=None) > start and f.start_time.replace(tzinfo=None) < end]
    expected = sum(f.size for f in files)
    have = sum(1 for f in files if (paths.raw / f"{stream}_{f.start_time:%Y%m%d_%H%M%S}.mp4").exists())
    print(f"   {len(files)} recording files cover the day, about {_size(expected)}"
          + (f"; {have} already downloaded" if have else ""))
    if not files:
        print("   nothing to download (the NVR may no longer keep this day)")

    index = []
    got = 0
    for i, f in enumerate(files, 1):
        dest = paths.raw / f"{stream}_{f.start_time:%Y%m%d_%H%M%S}.mp4"
        index.append({"path": dest.name, "start": f.start_time.isoformat(), "end": f.end_time.isoformat(), "stream": stream})
        label = f"   [{i}/{len(files)}] {f.start_time:%H:%M}"
        if dest.exists():
            if is_fragmented(dest):
                print(f"{label} already downloaded; making it seekable…")
                remux(dest, dest)
            print(f"{label} already downloaded, skipping")
            continue

        t0 = time.monotonic()
        last = [0.0]

        def progress(n, i=i, total=f.size, label=label):
            now = time.monotonic()
            emit("download", (i - 1 + min(1.0, n / max(total, 1))) / len(files), camera, f"file {i} of {len(files)}")
            if now - last[0] < 1:
                return
            last[0] = now
            rate = n / max(now - t0, 1e-6)
            _status(f"{label}: downloading {_size(n)} of ~{_size(total)} at {rate / 1e6:.1f} MB/s, "
                    f"{_dur(max(0, total - n) / max(rate, 1))} left")

        # 1. download to this Mac (the NVR serves whole files only)
        tmp = LOCAL_TMP / dest.name
        await nvrmod.download_file(nvr, ch, f, stream, tmp, progress)
        size = tmp.stat().st_size
        t_dl = time.monotonic() - t0
        print(f"\r{label}: downloaded {_size(size)} in {_dur(t_dl)} ({size / 1e6 / max(t_dl, 1e-6):.1f} MB/s)" + " " * 20)

        # 2. make it seekable: Reolink files have no index, so jumping into them means reading from the start
        t1 = time.monotonic()
        _status(f"{label}: making it seekable (lossless repackage on this Mac)…")
        fixed = LOCAL_TMP / ("seekable_" + dest.name)
        remux_local(tmp, fixed)
        tmp.unlink()
        print(f"\r{label}: made seekable in {_dur(time.monotonic() - t1)}" + " " * 40)

        # 3. one copy onto the share
        t2 = time.monotonic()
        dest.parent.mkdir(parents=True, exist_ok=True)
        copy_with_progress(fixed, dest, label)
        fixed.unlink()
        t_cp = time.monotonic() - t2
        print(f"\r{label}: copied to the share in {_dur(t_cp)} ({size / 1e6 / max(t_cp, 1e-6):.1f} MB/s); "
              f"file done in {_dur(time.monotonic() - t0)}" + " " * 20)
        got += size
    paths.files_index.write_text(json.dumps(index, indent=1))
    print(f"   {camera} finished: {_size(got)} downloaded in {_dur(time.monotonic() - cam_t0)}")
    emit("download", 1.0, camera, "done", force=True)


async def run(cameras: list[str], day: date, stream: str | None = None) -> None:
    """Fetch each camera; `stream` overrides the per-camera Fluent/Clear setting."""
    from .settings import load

    streams = load()["streams"]
    async with nvrmod.connect() as nvr:
        for cam in cameras:
            await fetch_camera(nvr, cam, day, stream or streams.get(cam, "sub"))
