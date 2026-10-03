"""ffmpeg-backed frame access over a day's recording files, addressed by seconds-since-midnight."""

from __future__ import annotations

import json
import queue
import shutil
import subprocess
import tempfile
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

import numpy as np

from .config import DayPaths


# Frames wider than this (Clear/main streams: 5120x1552, 3840x2160) are decoded on the Mac's hardware
# decoder and scaled down to it -- about 8x real time, versus 2-4x decoding them on the CPU.
MAX_WIDTH = 2560


@dataclass
class Recording:
    path: Path
    start: float  # seconds since local midnight of the analysed day (may be negative)
    end: float
    width: int  # as stored in the file
    height: int

    @property
    def big(self) -> bool:
        return self.width > MAX_WIDTH

    @property
    def size(self) -> tuple[int, int]:
        """Frame size the pipeline works at (scaled down for big streams, even dimensions)."""
        if not self.big:
            return self.width, self.height
        return MAX_WIDTH, int(round(self.height * MAX_WIDTH / self.width / 2)) * 2


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
    the file -- painfully slow over a network share. The rewrite happens on local disk
    (+faststart makes a second full pass to move the index to the front) and the result
    is copied to `dest` once, so a multi-GB Clear file crosses the network only one time.
    """
    with tempfile.TemporaryDirectory(prefix="reolapse-remux-") as d:
        tmp = Path(d) / dest.name
        remux_local(src, tmp)
        part = dest.with_name(dest.name + ".part")
        shutil.copyfile(tmp, part)
        part.replace(dest)


def remux_local(src: Path, out: Path) -> None:
    """The lossless rewrite itself (fragmented -> indexed, index at the front); `out` should be local."""
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-map", "0:v", "-map", "0:a?", "-c", "copy",
                    "-movflags", "+faststart", str(out)], check=True)


def _ffmpeg_frames(rec: Recording, args_in: list[str], vf: str) -> Iterator[np.ndarray]:
    w, h = rec.size
    if rec.big:  # hardware decode + scale, then download to system memory
        args_in = ["-hwaccel", "videotoolbox", "-hwaccel_output_format", "videotoolbox_vld", *args_in]
        vf = f"{vf},scale_vt=w={w}:h={h},hwdownload,format=nv12"
    cmd = ["ffmpeg", "-v", "error", *args_in, "-i", str(rec.path),
           "-an", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    # ffmpeg's messages are captured, not passed through: when the job is stopped (pause, cancel)
    # ffmpeg complains about the closed pipe, which is noise. They're reported only if ffmpeg
    # fails while we're still reading -- a real decode problem, e.g. a damaged recording.
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=w * h * 3 * 4)
    messages: deque[str] = deque(maxlen=12)
    reader = threading.Thread(target=lambda: messages.extend(
        line.decode(errors="replace").rstrip() for line in proc.stderr), daemon=True)
    reader.start()
    size = w * h * 3
    finished = False
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                finished = True
                break
            yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    finally:
        if finished:  # reached the end of what ffmpeg sent: did it end well?
            try:
                code = proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                code = None
            reader.join(timeout=2)
            if code not in (0, None):
                detail = "; ".join(m for m in messages if m) or f"exit code {code}"
                print(f"\n   warning: couldn't read part of {rec.path.name} (ffmpeg: {detail}); "
                      "the scan continues with the next part", flush=True)
        proc.stdout.close()
        if proc.poll() is None:
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
        for f in _ffmpeg_frames(r, args, f"fps={fps}"):
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
    """Fast sparse sampling: decode keyframes only, one frame per `every` seconds.

    Works for big streams too (hardware decode): an hour of Clear footage samples in 20-35 s.
    """
    for i, f in enumerate(_ffmpeg_frames(rec, ["-skip_frame", "nokey"], f"fps=1/{every}")):
        yield rec.start + i * every, f
