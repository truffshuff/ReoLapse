"""ffmpeg-backed frame access over a day's recording files, addressed by seconds-since-midnight."""

from __future__ import annotations

import json
import queue
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

import numpy as np

from .config import DayPaths


@dataclass
class Recording:
    path: Path
    start: float  # seconds since local midnight of the analysed day (may be negative)
    end: float
    width: int
    height: int


def day_seconds(iso: str, day_start: datetime) -> float:
    return (datetime.fromisoformat(iso).replace(tzinfo=None) - day_start).total_seconds()


def probe_size(path: Path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "json", str(path)], capture_output=True, text=True, check=True)
    s = json.loads(out.stdout)["streams"][0]
    return s["width"], s["height"]


def load_recordings(paths: DayPaths, probe: bool = True) -> list[Recording]:
    """The day's recording files. `probe=False` skips reading frame sizes (slow on the share)."""
    day_start = datetime(paths.day.year, paths.day.month, paths.day.day)
    recs = []
    for f in json.loads(paths.files_index.read_text()):
        p = paths.raw / f["path"]
        if probe and not p.exists():
            continue
        w, h = probe_size(p) if probe else (0, 0)
        recs.append(Recording(p, day_seconds(f["start"], day_start), day_seconds(f["end"], day_start), w, h))
    if not recs:
        raise SystemExit(f"No recordings under {paths.raw}; run `reolapse fetch` first")
    return recs


def is_fragmented(path: Path) -> bool:
    with open(path, "rb") as fh:
        return b"mvex" in fh.read(16384)


def remux(src: Path, dest: Path) -> None:
    """Rewrite Reolink's fragmented MP4 as a regular indexed one (lossless).

    Fragmented files have no global index, so every -ss seek scans from the start of
    the file -- painfully slow over a network share.
    """
    tmp = dest.with_name(dest.stem + ".remux.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-map", "0:v", "-map", "0:a?", "-c", "copy",
                    "-movflags", "+faststart", str(tmp)], check=True)
    tmp.replace(dest)


def _ffmpeg_frames(path: Path, w: int, h: int, args_in: list[str], vf: str) -> Iterator[np.ndarray]:
    cmd = ["ffmpeg", "-v", "error", *args_in, "-i", str(path),
           "-an", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=w * h * 3 * 4)
    size = w * h * 3
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    finally:
        proc.stdout.close()
        proc.kill()
        proc.wait()


def frames(recs: list[Recording], t0: float, t1: float, fps: float) -> Iterator[tuple[float, np.ndarray]]:
    """Yield (t, frame) at `fps` for day-seconds in [t0, t1), across file boundaries."""
    step = 1.0 / fps
    t = t0
    for r in recs:
        if r.end <= t or r.start >= t1:
            continue
        a = max(t, r.start)
        b = min(t1, r.end)
        if b - a < step:
            continue
        off = a - r.start
        args = ["-ss", f"{off:.3f}", "-t", f"{b - a:.3f}"]
        i = 0
        for f in _ffmpeg_frames(r.path, r.width, r.height, args, f"fps={fps}"):
            yield a + i * step, f
            i += 1
        t = a + i * step


def prefetch(it: Iterator, depth: int = 32) -> Iterator:
    """Run an iterator in a background thread so decoding overlaps with GPU work."""
    q: queue.Queue = queue.Queue(depth)
    done = object()

    def worker():
        try:
            for item in it:
                q.put(item)
        except BaseException as e:  # surface decode errors in the consumer
            q.put(e)
        q.put(done)

    threading.Thread(target=worker, daemon=True).start()
    while (item := q.get()) is not done:
        if isinstance(item, BaseException):
            raise item
        yield item


def keyframe_samples(rec: Recording, every: float) -> Iterator[tuple[float, np.ndarray]]:
    """Fast sparse sampling: decode keyframes only, one frame per `every` seconds."""
    i = 0
    for f in _ffmpeg_frames(rec.path, rec.width, rec.height, ["-skip_frame", "nokey"], f"fps=1/{every}"):
        yield rec.start + i * every, f
        i += 1
