"""Per camera-day analysis: time-of-day backgrounds and moving-object "tubes".

A tube is one object's continuous moving segment: a list of timestamped, alpha-masked
crops. Parked cars and other stationary detections never become tubes; an object that
stops for longer than its linger time ends its tube (and starts a new one if it moves again).
"""

from __future__ import annotations

import json
import pickle
import shutil
import time
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

import cv2
import numpy as np

from .config import CATEGORIES, MODEL_DIR, DayPaths
from .media import Recording, day_seconds, frames, keyframe_samples, load_recordings, prefetch

BG_SLOT = 600  # seconds per background
BG_SAMPLE = 60  # one sample every N seconds feeds the median

WINDOW_PAD = 2.0  # seconds added around each NVR event
WINDOW_GAP = 3.0  # merge events closer than this

PRE_ROLL = 2.0  # seconds of detections kept before an object starts moving
MOVE_SPAN = 1.5  # look-back used for the "is it moving" test
LOST_AFTER = 3.0  # close a track not seen for this long
MIN_TUBE = 1.0  # discard tubes shorter than this
LINGER = {"person": 8.0, "animal": 8.0, "vehicle": 4.0}  # stationary time before a tube ends

GATE_DIFF = 30  # grey-level change that counts as motion (640-wide, blurred)
GATE_MIN_PIXELS = 40  # changed pixels, after speckle removal, needed to wake the detector


# ---------------------------------------------------------------- backgrounds

def build_backgrounds(paths: DayPaths, recs: list[Recording], workers: int = 4) -> int:
    """Median of sparse keyframe samples per 10-minute slot.

    Each recording file is sampled in its own worker; slots wholly inside one file are
    finished there, and only slots straddling a file boundary are merged afterwards.
    """
    out = paths.backgrounds_out
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    def write(slot: int, samples: list[np.ndarray]) -> None:
        bg = np.median(np.stack(samples), axis=0).astype(np.uint8)
        cv2.imwrite(str(out / f"{slot:03d}.jpg"), bg, [cv2.IMWRITE_JPEG_QUALITY, 94])

    def sample(rec: Recording) -> dict[int, list[np.ndarray]]:
        buckets: dict[int, list[np.ndarray]] = {}
        for t, f in keyframe_samples(rec, BG_SAMPLE):
            if 0 <= t < 86400:
                buckets.setdefault(int(t // BG_SLOT), []).append(f.copy())
        partial = {}
        for slot, samples in buckets.items():
            if rec.start <= slot * BG_SLOT and (slot + 1) * BG_SLOT <= rec.end:
                write(slot, samples)
            else:
                partial[slot] = samples
        return partial

    leftovers: dict[int, list[np.ndarray]] = {}
    with ThreadPoolExecutor(workers) as pool:
        for i, partial in enumerate(pool.map(sample, recs), 1):
            for slot, samples in partial.items():
                leftovers.setdefault(slot, []).extend(samples)
            print(f"\r  backgrounds: {i}/{len(recs)} files", end="", flush=True)
    for slot, samples in leftovers.items():
        write(slot, samples)
    written = len(list(out.glob("*.jpg")))
    print(f"\r  backgrounds: {written} slots          ")
    return written


# ---------------------------------------------------------------- windows

def event_windows(paths: DayPaths, types: set[str]) -> list[tuple[float, float]]:
    if "all" in types:
        return [(0.0, 86400.0)]  # scan the whole day; the motion gate decides where YOLO runs
    day_start = datetime(paths.day.year, paths.day.month, paths.day.day)
    spans = sorted(
        (day_seconds(e["start"], day_start) - WINDOW_PAD, day_seconds(e["end"], day_start) + WINDOW_PAD)
        for e in json.loads(paths.events.read_text()) if e["type"] in types
    )
    merged: list[list[float]] = []
    for a, b in spans:
        a, b = max(a, 0.0), min(b, 86400.0)
        if b <= a:
            continue
        if merged and a - merged[-1][1] <= WINDOW_GAP:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


# ---------------------------------------------------------------- tracking

@dataclass
class Det:
    t: float
    box: tuple[int, int, int, int]  # x0, y0, x1, y1 of the crop
    crop: np.ndarray  # BGRA
    cls: str


@dataclass
class Track:
    buffer: deque = field(default_factory=deque)
    seg: list[Det] | None = None
    history: deque = field(default_factory=deque)  # (t, cx, cy, diag)
    still_since: float | None = None
    last_t: float = 0.0
    last: Det | None = None

    @staticmethod
    def category(dets: list[Det]) -> str:
        return CATEGORIES[Counter(d.cls for d in dets).most_common(1)[0][0]]

    def moving(self) -> bool:
        if len(self.history) < 2 or self.history[-1][0] - self.history[0][0] < MOVE_SPAN * 0.6:
            return False
        t0, x0, y0, _ = self.history[0]
        t1, x1, y1, diag = self.history[-1]
        return np.hypot(x1 - x0, y1 - y0) > max(6.0, 0.12 * diag)

    def update(self, d: Det) -> list[Det] | None:
        """Feed a detection; returns a finished segment if this update closed one."""
        x0, y0, x1, y1 = d.box
        self.history.append((d.t, (x0 + x1) / 2, (y0 + y1) / 2, float(np.hypot(x1 - x0, y1 - y0))))
        while self.history and d.t - self.history[0][0] > MOVE_SPAN:
            self.history.popleft()
        self.last_t = d.t
        self.last = d
        moving = self.moving()

        if self.seg is None:
            self.buffer.append(d)
            while self.buffer and d.t - self.buffer[0].t > PRE_ROLL:
                self.buffer.popleft()
            if moving:
                self.seg, self.still_since = list(self.buffer), None
                self.buffer.clear()
            return None

        self.seg.append(d)
        if moving:
            self.still_since = None
            return None
        if self.still_since is None:
            self.still_since = d.t
        if d.t - self.still_since > LINGER[self.category(self.seg)]:
            return self.close(trim_after=self.still_since + 1.0)
        return None

    def close(self, trim_after: float | None = None) -> list[Det] | None:
        seg, self.seg, self.still_since = self.seg, None, None
        self.buffer.clear()
        if not seg:
            return None
        if trim_after is not None:
            seg = [d for d in seg if d.t <= trim_after]
        if seg[-1].t - seg[0].t < MIN_TUBE:
            return None
        return seg


def _near(a, b, grow: float = 0.25) -> bool:
    """Does box b intersect box a grown by `grow` of its size on each side?"""
    gx, gy = (a[2] - a[0]) * grow, (a[3] - a[1]) * grow
    return b[0] < a[2] + gx and a[0] - gx < b[2] and b[1] < a[3] + gy and a[1] - gy < b[3]


def context_for(seg: list[Det], owner: Track, tracks, limit: int = 4) -> list[Det]:
    """Stationary objects the moving one came close to (e.g. the parked car someone reaches into).

    Parked objects never become tubes and may be missing from the 10-minute median background,
    so their latest crop rides along with the tube and is drawn underneath it.
    """
    out = []
    for tr in tracks:
        if tr is owner or tr.seg is not None or tr.last is None or tr.last.t < seg[0].t - 2.0:
            continue
        if any(_near(d.box, tr.last.box) for d in seg[::2]):
            out.append(tr.last)
    out.sort(key=lambda d: -(d.box[2] - d.box[0]) * (d.box[3] - d.box[1]))
    return out[:limit]


def _png(img: np.ndarray) -> bytes:
    return cv2.imencode(".png", img, [cv2.IMWRITE_PNG_COMPRESSION, 1])[1].tobytes()


def save_tube(paths: DayPaths, seg: list[Det], fps: float, context: list[Det] = ()) -> str:
    cat = Track.category(seg)
    frames_out = [(d.t, *d.box, _png(d.crop)) for d in seg]
    name = f"{seg[0].t:08.1f}_{cat}.pkl"
    tube = {"t0": seg[0].t, "t1": seg[-1].t, "category": cat,
            "cls": Counter(d.cls for d in seg).most_common(1)[0][0], "fps": fps, "frames": frames_out,
            "context": [(*d.box, _png(d.crop)) for d in context]}
    with open(paths.tubes_out / name, "wb") as fh:
        pickle.dump(tube, fh, protocol=pickle.HIGHEST_PROTOCOL)
    return cat


class MotionGate:
    """Cheap low-res frame differencing; YOLO only runs while something is changing."""

    def __init__(self, fps: float, width: int = 640, hold: float = 1.5):
        self.width = width
        self.lag = max(1, int(round(fps * 0.5)))
        self.prev: deque = deque(maxlen=self.lag + 1)
        self.hold = hold
        self.last_motion = -1e9
        self._kernel = np.ones((3, 3), np.uint8)

    def reset(self):
        self.prev.clear()
        self.last_motion = -1e9

    def __call__(self, t: float, frame: np.ndarray) -> bool:
        h, w = frame.shape[:2]
        small = cv2.cvtColor(cv2.resize(frame, (self.width, int(h * self.width / w)), interpolation=cv2.INTER_AREA),
                             cv2.COLOR_BGR2GRAY)
        small = cv2.GaussianBlur(small, (5, 5), 0)
        self.prev.append(small)
        if len(self.prev) <= self.lag:
            return True  # warm-up: let YOLO see the start of every window
        # Threshold, then remove single-pixel speckle (compression grain, night noise) before counting:
        # on Porch Left's 640x360 night stream this cut the gate from ~37% to <1% of frames while busy
        # afternoons still pass ~25-30%.
        diff = (cv2.absdiff(small, self.prev[0]) > GATE_DIFF).astype(np.uint8)
        diff = cv2.morphologyEx(diff, cv2.MORPH_OPEN, self._kernel)
        if int(np.count_nonzero(diff)) > GATE_MIN_PIXELS:
            self.last_motion = t
        return t - self.last_motion <= self.hold


BATCH = 8  # frames per YOLO call; batched fp16 on MPS is ~1.7x faster than per-frame model.track()


def _tracker():
    from ultralytics.trackers.byte_tracker import BYTETracker
    from ultralytics.utils import YAML, IterableSimpleNamespace
    from ultralytics.utils.checks import check_yaml

    return BYTETracker(IterableSimpleNamespace(**YAML.load(check_yaml("bytetrack.yaml"))))


def detect_tubes(paths: DayPaths, recs: list[Recording], windows, fps: float, model_name: str, imgsz: int) -> Counter:
    from ultralytics import YOLO

    if paths.tubes_out.exists():
        shutil.rmtree(paths.tubes_out)
    paths.tubes_out.mkdir(parents=True)

    model = YOLO(str(MODEL_DIR / model_name))
    keep = [i for i, n in model.names.items() if n in CATEGORIES]
    gate = MotionGate(fps)
    tracker = _tracker()
    stats: Counter = Counter()
    total = sum(b - a for a, b in windows)
    done = 0.0
    yolo_frames = all_frames = 0
    t_start = time.monotonic()
    last_report = 0.0

    def report(cur: float, wi: int):
        el = time.monotonic() - t_start
        eta = el / cur * (total - cur) if cur else 0
        print(f"\r  window {wi + 1}/{len(windows)}  {cur / 60:6.1f}/{total / 60:.1f} min of footage  "
              f"yolo {yolo_frames}/{all_frames} frames  tubes {sum(stats.values())}  "
              f"elapsed {el / 60:4.1f}m eta {eta / 60:4.1f}m   ", end="", flush=True)

    for wi, (a, b) in enumerate(windows):
        tracks: dict[int, Track] = {}
        gate.reset()
        tracker.reset()
        batch: list[tuple[float, np.ndarray]] = []

        def finish(seg, owner: Track):
            if seg:
                stats[save_tube(paths, seg, fps, context_for(seg, owner, tracks.values()))] += 1

        def expire(t: float):
            for tid in [k for k, tr in tracks.items() if t - tr.last_t > LOST_AFTER]:
                tr = tracks.pop(tid)
                finish(tr.close(), tr)

        def flush():
            if not batch:
                return
            results = model.predict([f for _, f in batch], imgsz=imgsz, conf=0.35, classes=keep, device="mps",
                                    quantize=16, verbose=False)
            for (t, frame), r in zip(batch, results):
                if len(r.boxes) and r.masks is not None:
                    H, W = frame.shape[:2]
                    xyxys, polys = r.boxes.xyxy.cpu().numpy(), r.masks.xy
                    for x_, y_, x2_, y2_, tid, _score, cls_i, idx in tracker.update(r.boxes.cpu().numpy(), frame):
                        idx = int(idx)
                        poly = polys[idx]
                        if len(poly) < 3:
                            continue
                        bx = xyxys[idx]
                        x0, y0 = max(0, int(bx[0]) - 2), max(0, int(bx[1]) - 2)
                        x1, y1 = min(W, int(bx[2]) + 3), min(H, int(bx[3]) + 3)
                        if x1 - x0 < 4 or y1 - y0 < 4:
                            continue
                        alpha = np.zeros((y1 - y0, x1 - x0), np.uint8)
                        cv2.fillPoly(alpha, [(poly - (x0, y0)).astype(np.int32)], 255)
                        crop = np.dstack([frame[y0:y1, x0:x1], alpha])
                        det = Det(t, (x0, y0, x1, y1), crop, model.names[int(cls_i)])
                        tr = tracks.setdefault(int(tid), Track())
                        finish(tr.update(det), tr)
                expire(t)
            batch.clear()

        for t, frame in prefetch(frames(recs, a, b, fps)):
            all_frames += 1
            if gate(t, frame):
                yolo_frames += 1
                batch.append((t, frame))
                if len(batch) >= BATCH:
                    flush()
            else:
                flush()
                expire(t)
            if t - last_report > 60:
                last_report = t
                report(done + (t - a), wi)
        flush()
        for tr in list(tracks.values()):
            finish(tr.close(), tr)
        done += b - a
        report(done, wi)
    print()
    return stats


def run(cameras: list[str], day, *, fps: float, model: str, imgsz: int, types: set[str], skip_bg: bool,
        hours: tuple[float, float] = (0, 24)) -> None:
    for cam in cameras:
        paths = DayPaths(cam, day)
        print(f"== {cam} {day}")
        recs = load_recordings(paths)
        if not skip_bg:
            build_backgrounds(paths, recs)
        windows = [(max(a, hours[0] * 3600), min(b, hours[1] * 3600)) for a, b in event_windows(paths, types)]
        windows = [(a, b) for a, b in windows if b > a]
        print(f"  {len(windows)} event windows, {sum(b - a for a, b in windows) / 60:.1f} min of footage to scan")
        stats = detect_tubes(paths, recs, windows, fps, model, imgsz)
        print(f"  tubes: {dict(stats)}")
