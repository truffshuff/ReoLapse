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
from pathlib import Path

import cv2
import numpy as np

from . import tube_index
from .progress import clock, dur, emit
from .config import CATEGORIES, MODEL_DIR, DayPaths, stream_of
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
            print(f"\r   building backgrounds: {i} of {len(recs)} recording files read", end="", flush=True)
            emit("backgrounds", i / len(recs), paths.camera, f"file {i} of {len(recs)}")
    for slot, samples in leftovers.items():
        write(slot, samples)
    written = len(list(out.glob("*.jpg")))
    if recs:
        mark_backgrounds_complete(out, recs[0].size)
    print(f"\r   built {written} background images (one per 10 minutes of the day)" + " " * 20)
    return written


BG_DONE = ".complete"  # marker in the backgrounds folder: all slots built, with the frame size


def mark_backgrounds_complete(out: Path, size: tuple[int, int]) -> None:
    (out / BG_DONE).write_text(json.dumps({"size": list(size)}))


def backgrounds_complete(paths: DayPaths, size: tuple[int, int]) -> bool:
    f = paths.backgrounds / BG_DONE
    try:
        return json.loads(f.read_text())["size"] == list(size)
    except (OSError, ValueError, KeyError):
        return False


class InlineBackgrounds:
    """Builds the 10-minute background medians from the frames the object scan decodes anyway,
    so a whole-day scan reads each recording once instead of twice (big win for Clear footage).
    Slots that already exist (e.g. before a pause) are left alone."""

    def __init__(self, out: Path):
        self.out = out
        out.mkdir(parents=True, exist_ok=True)
        self.pending: dict[int, list[np.ndarray]] = {}
        self.next_t = -1.0

    def offer(self, t: float, frame: np.ndarray) -> None:
        if t < self.next_t or not 0 <= t < 86400:
            return
        self.next_t = (t // BG_SAMPLE + 1) * BG_SAMPLE
        slot = int(t // BG_SLOT)
        if not (self.out / f"{slot:03d}.jpg").exists():
            self.pending.setdefault(slot, []).append(frame.copy())
        self._flush(below=slot)

    def _flush(self, below: int) -> None:
        for slot in sorted(s for s in self.pending if s < below):
            samples = self.pending.pop(slot)
            bg = np.median(np.stack(samples), axis=0).astype(np.uint8)
            cv2.imwrite(str(self.out / f"{slot:03d}.jpg"), bg, [cv2.IMWRITE_JPEG_QUALITY, 94])

    def finish(self) -> None:
        self._flush(below=10**9)


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

# A person the detector struggles with (bent over, cut off by the frame edge, washed out by IR) gets
# called a dog or a bird in many frames, but rarely the other way round: on Oct 1 at 19:16 the nano
# model said person in 12 of 25 frames and bird/dog in 10, and the plain majority went to "bird".
# So an "animal" track that is a person in at least a third of its frames counts as a person.
PERSON_SHARE = 1 / 3


def vote(classes: Counter) -> str:
    """An object's category from its per-frame detector classes."""
    by_cat = Counter()
    for cls, n in classes.items():
        by_cat[CATEGORIES[cls]] += n
    cat = by_cat.most_common(1)[0][0]
    if cat == "animal" and by_cat["person"] >= PERSON_SHARE * sum(by_cat.values()):
        return "person"
    return cat


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
        return vote(Counter(d.cls for d in dets))

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


def save_tube(paths: DayPaths, seg: list[Det], fps: float, context: list[Det] = ()) -> dict | None:
    """Write one tube file; returns its index entry (see tube_index), or None if the object never
    really travelled (a parked car whose detection box wobbled)."""
    cat = Track.category(seg)
    if not tube_index.really_moved(cat, [d.box for d in seg]):
        return None
    frames_out = [(d.t, *d.box, _png(d.crop)) for d in seg]
    name, n = f"{seg[0].t:08.1f}_{cat}.pkl", 1
    while (paths.tubes_out / name).exists():  # two objects can start in the same tenth of a second
        n += 1
        name = f"{seg[0].t:08.1f}_{cat}_{n}.pkl"
    votes = Counter(d.cls for d in seg)
    tube = {"t0": seg[0].t, "t1": seg[-1].t, "category": cat, "cls": votes.most_common(1)[0][0],
            "votes": dict(votes), "fps": fps, "frames": frames_out,
            "context": [(*d.box, _png(d.crop)) for d in context]}
    with open(paths.tubes_out / name, "wb") as fh:
        pickle.dump(tube, fh, protocol=pickle.HIGHEST_PROTOCOL)
    return tube_index.tube_meta(name, seg[0].t, seg[-1].t, cat, [(d.t, *d.box) for d in seg])


class MotionGate:
    """Cheap low-res frame differencing; YOLO only runs while something is changing."""

    def __init__(self, fps: float, width: int = 640, hold: float = 1.5, ignore: list | None = None):
        self.width = width
        self.ignore = ignore or []  # ignore zones (0..1 boxes): motion there doesn't count
        self._mask = None
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
        if self.ignore:
            if self._mask is None or self._mask.shape != diff.shape:
                from .review import gate_mask

                self._mask = gate_mask(self.ignore, diff.shape[1], diff.shape[0])
            diff &= self._mask
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


CHECKPOINT = "checkpoint.json"
CHECKPOINT_EVERY = 120.0  # seconds of footage between checkpoints


def _resume_point(paths: DayPaths, key: dict) -> dict | None:
    """A checkpoint from an interrupted scan with the same settings, else None."""
    try:
        ck = json.loads((paths.tubes_out / CHECKPOINT).read_text())
    except (OSError, ValueError):
        return None
    return ck if ck.get("key") == key else None


def _release_gpu_cache() -> None:
    """Hand PyTorch's cached GPU memory back to macOS (GPU memory is the Mac's RAM). Called at each
    checkpoint: a day-long scan's cache can otherwise creep up until the Mac swaps, which slowed a
    Porch Left scan to a third of its speed with the process at 14 GB."""
    try:
        import torch

        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
    except Exception:
        pass


def _no_nms_time_limit() -> None:
    """Turn off Ultralytics' NMS time limit (2 s + 0.05 s per image).

    On MPS the model runs asynchronously, so the NMS timer also counts the wait for the forward
    pass; with a large model at 1920 px a batch of 8 takes about as long as the limit. When it trips,
    NMS stops and every later image in the batch comes back with no detections at all ("NMS time
    limit 2.400s exceeded"), a hole of up to 0.7 s in every track at 10 fps.
    """
    from ultralytics.utils import nms

    if getattr(nms.non_max_suppression, "_no_time_limit", False):
        return
    original = nms.non_max_suppression

    def non_max_suppression(*args, **kwargs):
        kwargs["max_time_img"] = 1e6
        return original(*args, **kwargs)

    non_max_suppression._no_time_limit = True
    nms.non_max_suppression = non_max_suppression


def detect_tubes(paths: DayPaths, recs: list[Recording], windows, fps: float, model_name: str, imgsz: int,
                 backgrounds: InlineBackgrounds | None = None) -> Counter:
    """Scan `windows` for moving objects, writing tubes as they finish.

    Resumable: a checkpoint (tubes saved so far, the time scanned up to) is written every
    CHECKPOINT_EVERY seconds of footage; if the job is stopped (pause, sleep, lost share) the
    next run with the same settings keeps those tubes and continues from that time. Objects
    that were still being tracked at that moment are picked up again from there.
    """
    from ultralytics import YOLO

    key = {"windows": [[round(a, 1), round(b, 1)] for a, b in windows], "fps": fps, "model": model_name,
           "imgsz": imgsz, "size": list(recs[0].size)}
    resume = _resume_point(paths, key)
    metas: list[dict] = []
    if resume:
        keep_files = {m["id"] + ".pkl" for m in resume["metas"]}
        for p in paths.tubes_out.glob("*.pkl"):
            if p.name not in keep_files:  # saved after the checkpoint: it will be found again
                p.unlink()
        metas = list(resume["metas"])
        resumed_at = resume["t"]
        windows = [(max(a, resumed_at), b) for a, b in windows if b > resumed_at]
        print(f"   resuming an interrupted scan from {clock(resumed_at)}: {len(metas)} objects already found")
    else:
        if paths.tubes_out.exists():
            shutil.rmtree(paths.tubes_out)
        paths.tubes_out.mkdir(parents=True)

    def checkpoint(t: float) -> None:
        tmp = paths.tubes_out / (CHECKPOINT + ".tmp")
        tmp.write_text(json.dumps({"key": key, "t": t, "metas": metas}, separators=(",", ":")))
        tmp.replace(paths.tubes_out / CHECKPOINT)
        _release_gpu_cache()

    _no_nms_time_limit()
    model = YOLO(str(MODEL_DIR / model_name))
    keep = [i for i, n in model.names.items() if n in CATEGORIES]
    from .review import zones_for

    gate = MotionGate(fps, ignore=zones_for(paths.camera))
    tracker = _tracker()
    stats: Counter = Counter(m["category"] for m in metas)
    total = sum(b - a for a, b in windows)
    last_checkpoint = windows[0][0] if windows else 0.0
    done = 0.0
    yolo_frames = all_frames = 0
    t_start = time.monotonic()
    last_report = 0.0

    def report(cur: float, wi: int):
        el = time.monotonic() - t_start
        eta = el / cur * (total - cur) if cur else 0
        emit("analyze", cur / total if total else 1.0, paths.camera,
             f"{sum(stats.values())} objects · about {eta / 60:.0f} min left")
        share = 100 * yolo_frames / max(all_frames, 1)
        where = f"up to {clock(windows[0][0] + cur)}" if len(windows) == 1 else f"window {wi + 1} of {len(windows)}"
        print(f"\r   scanned {where} · {sum(stats.values())} objects so far · detector ran on {share:.0f}% of frames"
              f" · {dur(el)} elapsed, about {dur(eta)} left   ", end="", flush=True)

    for wi, (a, b) in enumerate(windows):
        tracks: dict[int, Track] = {}
        gate.reset()
        tracker.reset()
        batch: list[tuple[float, np.ndarray]] = []

        def finish(seg, owner: Track):
            if seg:
                meta = save_tube(paths, seg, fps, context_for(seg, owner, tracks.values()))
                if meta is not None:
                    metas.append(meta)
                    stats[meta["category"]] += 1

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
            if backgrounds is not None:
                backgrounds.offer(t, frame)
            if t - last_checkpoint >= CHECKPOINT_EVERY:
                checkpoint(t)
                last_checkpoint = t
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
        checkpoint(b)
        last_checkpoint = b
        report(done, wi)
    print()
    if backgrounds is not None:
        backgrounds.finish()
    # objects kept before this check existed (e.g. from a resumed scan) get the same test
    metas, dropped = tube_index.prune(paths.tubes_out, metas, recs[0].size)
    if dropped:
        print(f"   set aside {dropped} detections that never really moved (parked cars and the like)")
    stats = Counter(m["category"] for m in metas)
    (paths.tubes_out / CHECKPOINT).unlink(missing_ok=True)  # complete: a later run starts fresh
    return stats


def _backgrounds_match(paths: DayPaths, recs: list[Recording]) -> bool:
    sample = next(iter(sorted(paths.backgrounds.glob("*.jpg"))), None) if paths.backgrounds.is_dir() else None
    if sample is None:
        return False
    img = cv2.imread(str(sample))
    return img is not None and (img.shape[1], img.shape[0]) == recs[0].size


ANALYSIS_INFO = "analysis.json"


def write_analysis_info(paths: DayPaths, recs: list[Recording], **settings) -> None:
    """Record how this day was analyzed, next to its objects, so the app can show it."""
    import os

    info = {"stream": stream_of(r.path.name for r in recs), "source_size": [recs[0].width, recs[0].height],
            "size": list(recs[0].size), **settings, "types": sorted(settings.get("types", [])),
            "hours": list(settings.get("hours", (0, 24))),
            "finished": datetime.now().isoformat(timespec="seconds"), "finished_ts": time.time(),
            "app": os.environ.get("REOLAPSE_APP_VERSION")}
    (paths.tubes_out / ANALYSIS_INFO).write_text(json.dumps(info, indent=1))


def finished_since(paths: DayPaths, since: float | None, **settings) -> bool:
    """Was this camera-day analyzed to the end, with these settings, after `since`? A job resumed after
    a pause must not redo (and so delete) a camera it already finished."""
    if since is None:
        return False
    try:
        info = json.loads((paths.tubes_out / ANALYSIS_INFO).read_text())
    except (OSError, ValueError):
        # Finished by a version that didn't write analysis.json: a complete index (written at the end)
        # with no checkpoint (removed at the end) from after the job was queued.
        index = paths.tubes_out / tube_index.INDEX_NAME
        return (index.exists() and not (paths.tubes_out / CHECKPOINT).exists()
                and index.stat().st_mtime >= since)
    same = (info.get("fps") == settings["fps"] and info.get("model") == settings["model"]
            and info.get("imgsz") == settings["imgsz"] and info.get("types") == sorted(settings["types"])
            and info.get("hours") == list(settings["hours"]))
    return same and info.get("finished_ts", 0) >= since


def run(cameras: list[str], day, *, fps: float, model: str, imgsz: int, types: set[str], skip_bg: bool,
        hours: tuple[float, float] = (0, 24), done_since: float | None = None,
        per_camera: dict | None = None) -> None:
    """per_camera: {camera: {"fps"?, "model"?, "imgsz"?}} overriding the settings for that camera."""
    defaults = (fps, model, imgsz)
    for cam in cameras:
        own = (per_camera or {}).get(cam, {})
        fps, model, imgsz = float(own.get("fps", defaults[0])), own.get("model", defaults[1]), int(own.get("imgsz", defaults[2]))
        paths = DayPaths(cam, day)
        cam_t0 = time.monotonic()
        print(f"== {cam}: finding moving objects on {day}" + (" (with this camera's own settings)" if own else ""))
        if finished_since(paths, done_since, fps=fps, model=model, imgsz=imgsz, types=types, hours=hours):
            print("   already finished earlier in this job with the same settings; keeping those results")
            continue
        recs = load_recordings(paths)
        w, h = recs[0].size
        how = (f"Clear stream scaled to {w}×{h} on the hardware decoder" if recs[0].big
               else f"{w}×{h}")
        print(f"   footage: {len(recs)} recording files, {how}")
        size = recs[0].size
        if paths.backgrounds.is_dir() and not _backgrounds_match(paths, recs) and any(paths.backgrounds.glob("*.jpg")):
            print("   the saved backgrounds don't match this footage's size (stream changed?), so rebuilding them")
            shutil.rmtree(paths.backgrounds_out, ignore_errors=True)
            skip_bg = False
        whole_day = "all" in types and hours == (0, 24)
        inline = None
        if backgrounds_complete(paths, size) or (skip_bg and _backgrounds_match(paths, recs)):
            print("   reusing this day's background images")
        elif whole_day:
            print("   background images (the empty scene for every 10 minutes) are built during the scan,"
                  " so each recording is read only once")
            inline = InlineBackgrounds(paths.backgrounds_out)
        else:
            print("   building background images: the empty scene for every 10 minutes, from one frame a minute…")
            build_backgrounds(paths, recs)
        windows = [(max(a, hours[0] * 3600), min(b, hours[1] * 3600)) for a, b in event_windows(paths, types)]
        windows = [(a, b) for a, b in windows if b > a]
        span = sum(b - a for a, b in windows)
        if "all" in types:
            print(f"   scanning the whole day at {fps:g} frames/s; the detector ({model}, {imgsz} px) only runs "
                  "while something in the picture changes")
        else:
            print(f"   scanning {len(windows)} NVR event windows ({dur(span)} of footage) at {fps:g} frames/s "
                  f"with {model}")
        stats = detect_tubes(paths, recs, windows, fps, model, imgsz, backgrounds=inline)
        if inline is not None:
            mark_backgrounds_complete(paths.backgrounds_out, size)
        write_analysis_info(paths, recs, fps=fps, model=model, imgsz=imgsz, types=types, hours=hours)
        found = ", ".join(f"{n} {k}{'s' if n != 1 and k != 'person' else ''}".replace("person", "people" if n != 1 else "person")
                          for k, n in stats.most_common()) or "nothing that moved"
        print(f"   {cam} finished in {dur(time.monotonic() - cam_t0)}: {sum(stats.values())} moving objects ({found})")
