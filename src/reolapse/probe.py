"""Connectivity / data-shape probe: what does the NVR give us for a day?"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from . import nvr as nvrmod


async def run(cameras: list[str], day: date) -> None:
    async with nvrmod.connect() as nvr:
        print(f"NVR {nvr.nvr_name} model={nvr.model} tz={nvr.timezone()}")
        for ch in nvr.channels:
            print(f"  ch{ch:<3} {nvr.camera_name(ch)!r:28} {nvr.camera_model(ch)}")

        start = datetime(day.year, day.month, day.day)
        end = start + timedelta(days=1) - timedelta(seconds=1)
        for cam in cameras:
            ch = nvrmod.find_channel(nvr, cam)
            print(f"\n== {cam} (ch{ch}) {day} ==")
            by_trig = await nvrmod.event_files(nvr, ch, start, end)
            for trig, files in sorted(by_trig.items(), key=lambda kv: kv[0].value):
                durs = sorted(f.duration.total_seconds() for f in files)
                print(f"  {trig.name:10} n={len(files):4}  total={sum(durs)/60:6.1f}min  "
                      f"median={durs[len(durs)//2]:5.0f}s  max={durs[-1]:6.0f}s")
            for stream in ("sub", "main"):
                files = await nvrmod.recording_files(nvr, ch, start, end, stream)
                size = sum(f.size for f in files)
                print(f"  {stream:4} recordings: {len(files)} files, ~{size / 1e9:.1f} GB")
