"""Turn a camera-day's tubes into a synopsis video (option B) and summary stills (option C)."""

from __future__ import annotations

import bisect
import json
import pickle
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .analyze import BG_SLOT
from .config import DayPaths
from .progress import dur, emit

CATEGORY_BGR = {"person": (40, 150, 255), "vehicle": (255, 180, 50), "animal": (80, 220, 80)}

# Cyclic 24h palette (RGB keyframes), bright enough to read on day and IR-night backgrounds.
_TIME_KEYS = [(0, (100, 110, 255)), (6, (230, 90, 220)), (9, (255, 150, 40)), (12, (255, 230, 50)),
              (15, (150, 230, 60)), (18, (40, 210, 230)), (21, (60, 140, 255)), (24, (100, 110, 255))]

GRID_X, GRID_Y, BIN = 48, 16, 0.5  # placement occupancy grid (cells) and time bin (output seconds)


def time_bgr(day_t: float) -> tuple[int, int, int]:
    h = (day_t / 3600.0) % 24
    for (h0, c0), (h1, c1) in zip(_TIME_KEYS, _TIME_KEYS[1:]):
        if h0 <= h <= h1:
            f = (h - h0) / (h1 - h0)
            r, g, b = (int(a + (b_ - a) * f) for a, b_ in zip(c0, c1))
            return b, g, r
    return 255, 255, 255


def clock(day_t: float, seconds: bool = False) -> str:
    h, m, s = int(day_t // 3600) % 24, int(day_t % 3600 // 60), int(day_t % 60)
    suffix = "a" if h < 12 else "p"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d}:{s:02d}{suffix}" if seconds else f"{h12}:{m:02d}{suffix}"


# ---------------------------------------------------------------- data

@dataclass
class Tube:
    t0: float
    t1: float
    category: str
    times: list[float]
    boxes: list[tuple[int, int, int, int]]
    pngs: list[bytes]
    context: list = field(default_factory=list)  # [(x0, y0, x1, y1, png)] stationary objects it was near
    speed: float = 1.0  # real seconds per output second
    start: float = 0.0  # placement in the output video (seconds)

    @property
    def out_duration(self) -> float:
        return (self.t1 - self.t0) / self.speed

    def frame_at(self, tau: float) -> int | None:
        """Index of the crop to show at output time tau, or None."""
        target = self.t0 + (tau - self.start) * self.speed
        if target < self.t0 or target > self.t1:
            return None
        i = bisect.bisect_right(self.times, target) - 1
        if i < 0 or target - self.times[i] > 0.6:
            return None  # gap in the track (motion gate closed)
        return i

    def crop(self, i: int) -> np.ndarray:
        return cv2.imdecode(np.frombuffer(self.pngs[i], np.uint8), cv2.IMREAD_UNCHANGED)


def load_tubes(paths: DayPaths, only: set[str] | None) -> list[Tube]:
    tubes = []
    for p in sorted(paths.tubes.glob("*.pkl")):
        with open(p, "rb") as fh:
            d = pickle.load(fh)
        if only and d["category"] not in only:
            continue
        fr = d["frames"]
        tubes.append(Tube(d["t0"], d["t1"], d["category"], [f[0] for f in fr],
                          [tuple(f[1:5]) for f in fr], [f[5] for f in fr], context=d.get("context", [])))
    return tubes


class Backgrounds:
    def __init__(self, folder: Path):
        self.slots = {int(p.stem): p for p in folder.glob("*.jpg")}
        if not self.slots:
            raise SystemExit(f"No backgrounds in {folder}; run `reolapse analyze` first")
        self.keys = sorted(self.slots)
        self._cache: dict[int, np.ndarray] = {}

    def _img(self, slot: int) -> np.ndarray:
        if slot not in self._cache:
            if len(self._cache) > 8:
                self._cache.pop(next(iter(self._cache)))
            self._cache[slot] = cv2.imread(str(self.slots[slot]))
        return self._cache[slot]

    @property
    def shape(self):
        return self._img(self.keys[0]).shape

    def at(self, day_t: float) -> np.ndarray:
        """Cross-fade between the two nearest slot medians (slot centres at slot*600+300)."""
        x = (day_t - BG_SLOT / 2) / BG_SLOT
        i = bisect.bisect_right(self.keys, x) - 1
        if i < 0:
            return self._img(self.keys[0]).copy()
        if i >= len(self.keys) - 1:
            return self._img(self.keys[-1]).copy()
        a, b = self.keys[i], self.keys[i + 1]
        f = float(np.clip((x - a) / (b - a), 0, 1))
        return cv2.addWeighted(self._img(a), 1 - f, self._img(b), f, 0)

    def daylight(self) -> np.ndarray:
        """The background nearest early afternoon -- a neutral base for the stills."""
        target = 13 * 3600 / BG_SLOT
        return self._img(min(self.keys, key=lambda k: abs(k - target))).copy()


# ---------------------------------------------------------------- placement

def place(tubes: list[Tube], length: float, shape, jitter: float) -> float:
    """Assign each tube an output start near its time-of-day position, avoiding overlaps.

    Longest tubes are placed first; each candidate start within ±jitter of the ideal
    position is scored by how many occupied grid cells it would collide with.
    Returns the actual video length.
    """
    H, W = shape[:2]
    cw, ch = W / GRID_X, H / GRID_Y
    max_d = max((t.out_duration for t in tubes), default=0)
    nbins = int((length + jitter + max_d) / BIN) + 2
    occ = np.zeros((nbins, GRID_Y, GRID_X), np.int16)
    k = max(0, int(jitter / BIN))
    steps = np.arange(-k, k + 1) * BIN  # always includes 0 (the true time)

    for t in sorted(tubes, key=lambda t: -t.out_duration):
        n = int(t.out_duration / BIN) + 1
        fp = np.zeros((n, GRID_Y, GRID_X), bool)
        for tt, (x0, y0, x1, y1) in zip(t.times, t.boxes):
            b = min(n - 1, int((tt - t.t0) / t.speed / BIN))
            fp[b, int(y0 / ch):int(y1 / ch) + 1, int(x0 / cw):int(x1 / cw) + 1] = True
        area = max(1, int(fp.sum()))
        ideal = t.t0 / 86400.0 * length
        best, best_cost = ideal, None
        for s in ideal + steps:
            if s < 0 or s + t.out_duration > length + jitter:
                continue
            b0 = int(s / BIN)
            # overlap (in units of this object's own footprint), plus a penalty for drifting from its true
            # time: sliding the full allowance only pays off if it removes half a footprint of overlap
            cost = occ[b0:b0 + n][fp].sum() / area + 0.5 * abs(s - ideal) / max(jitter, BIN)
            if best_cost is None or cost < best_cost:
                best, best_cost = s, cost
        t.start = max(0.0, best)
        b0 = int(t.start / BIN)
        occ[b0:b0 + n] += fp
    return max([length] + [t.start + t.out_duration for t in tubes])


# ---------------------------------------------------------------- drawing

def composite(canvas: np.ndarray, crop: np.ndarray, box, opacity: float, outline_bgr=None) -> None:
    x0, y0 = box[0], box[1]
    h, w = crop.shape[:2]
    h, w = min(h, canvas.shape[0] - y0), min(w, canvas.shape[1] - x0)
    if h <= 0 or w <= 0:
        return
    crop = crop[:h, :w]
    alpha = cv2.GaussianBlur(crop[:, :, 3], (5, 5), 0).astype(np.float32)[:, :, None] * (opacity / 255.0)
    region = canvas[y0:y0 + h, x0:x0 + w]
    region[:] = (region * (1 - alpha) + crop[:, :, :3] * alpha).astype(np.uint8)
    if outline_bgr is not None:
        cnts, _ = cv2.findContours((crop[:, :, 3] > 127).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(region, cnts, -1, outline_bgr, 2, cv2.LINE_AA)


def label(canvas: np.ndarray, text: str, x: int, y: int, bgr, scale: float) -> None:
    font = cv2.FONT_HERSHEY_SIMPLEX
    th = max(1, int(round(scale * 2)))
    (tw, tht), base = cv2.getTextSize(text, font, scale, th)
    x = int(np.clip(x, 0, canvas.shape[1] - tw - 6))
    y = int(np.clip(y, tht + 6, canvas.shape[0] - 2))
    cv2.rectangle(canvas, (x, y - tht - 6), (x + tw + 6, y + 2), bgr, -1)
    lum = 0.114 * bgr[0] + 0.587 * bgr[1] + 0.299 * bgr[2]
    cv2.putText(canvas, text, (x + 3, y - 2), font, scale, (0, 0, 0) if lum > 140 else (255, 255, 255), th, cv2.LINE_AA)


def chip(canvas: np.ndarray, text: str, scale: float) -> int:
    """Dark text chip over the camera's own OSD timestamp (bottom-left). Returns its width."""
    H, W = canvas.shape[:2]
    font = cv2.FONT_HERSHEY_SIMPLEX
    (tw, th), _ = cv2.getTextSize(text, font, scale * 1.4, 2)
    chip_w, chip_h = max(int(W * 0.2), tw + 24), th + 22
    cv2.rectangle(canvas, (0, H - chip_h - 8), (chip_w, H), (20, 20, 20), -1)
    cv2.putText(canvas, text, (12, H - 16), font, scale * 1.4, (255, 255, 255), 2, cv2.LINE_AA)
    return chip_w


def hud(canvas: np.ndarray, day_t: float, day_label: str, density: np.ndarray, scale: float) -> None:
    """Clock chip and a day timeline."""
    H, W = canvas.shape[:2]
    chip_w = chip(canvas, f"{day_label}  {clock(day_t)}", scale)
    # timeline: event density ticks + playhead
    y = H - 6
    xs = np.linspace(chip_w + 10, W - 10, len(density)).astype(int)
    cv2.line(canvas, (xs[0], y), (xs[-1], y), (60, 60, 60), 3)
    for x, d in zip(xs, density):
        if d > 0:
            cv2.line(canvas, (x, y), (x, y - int(4 + 10 * d)), (200, 200, 200), 1)
    px = int(xs[0] + (xs[-1] - xs[0]) * day_t / 86400.0)
    cv2.circle(canvas, (px, y), 5, time_bgr(day_t), -1, cv2.LINE_AA)


# ---------------------------------------------------------------- synopsis video

def render_video(paths: DayPaths, tubes: list[Tube], bgs: Backgrounds, *, style: str, color: str, length: float | None,
                 density: float, speed: float, max_tube: float, fps: float, labels: bool, tag: str,
                 drift_min: float = 10.0) -> Path:
    H, W = bgs.shape[:2]
    for t in tubes:
        real = t.t1 - t.t0
        t.speed = max(speed, real / max_tube)  # very long tubes play faster so they don't dominate
    if length is None:
        length = float(np.clip(sum(t.out_duration for t in tubes) / density, 60, 900))
    jitter = length * drift_min * 60 / 86400.0  # max shift, as output seconds
    total = place(tubes, length, (H, W), jitter)
    hist = np.histogram([t.t0 for t in tubes], bins=96, range=(0, 86400))[0].astype(float)
    hist = hist / hist.max() if hist.max() else hist

    paths.out.mkdir(parents=True, exist_ok=True)
    dest = paths.out / f"synopsis_{tag}.mp4"
    nframes = int(total * fps)
    enc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(fps),
         "-i", "-", "-c:v", "h264_videotoolbox", "-b:v", "12M", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
         str(dest)], stdin=subprocess.PIPE)

    order = sorted(tubes, key=lambda t: t.start)
    starts = [t.start for t in order]
    active: list[Tube] = []
    nxt = 0
    scale = W / 1920 * 0.55
    day_label = paths.day.strftime("%a %b %-d")
    style_name = tag.split("_")[0]
    print(f"   {style_name} video: {len(tubes)} objects in {dur(total)} of video ({nframes} frames)…")
    t_start = time.monotonic()
    for k in range(nframes):
        tau = k / fps
        while nxt < len(order) and starts[nxt] <= tau:
            active.append(order[nxt])
            nxt += 1
        active = [t for t in active if t.start + t.out_duration >= tau]
        day_t = min(86399.0, tau / length * 86400.0)
        canvas = bgs.at(day_t)
        shown = []
        visible = [(t, t.frame_at(tau)) for t in sorted(active, key=lambda t: t.t0)]  # older objects underneath
        for t, i in visible:  # parked cars etc. that these objects interacted with go underneath everything
            if i is not None:
                for *box, png in t.context:
                    composite(canvas, cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_UNCHANGED), box,
                              0.55 if style == "ghost" else 1.0)
        for t, i in visible:
            if i is None:
                continue
            col = CATEGORY_BGR[t.category] if color == "category" else time_bgr(t.t0)
            composite(canvas, t.crop(i), t.boxes[i], 0.55 if style == "ghost" else 1.0,
                      col if style == "outline" else None)
            shown.append((t, i, col))
        if labels:
            for t, i, col in shown:
                x0, y0 = t.boxes[i][:2]
                label(canvas, clock(t.times[i]), x0, y0 - 2, col, scale)  # real time of this frame
        hud(canvas, day_t, day_label, hist, scale)
        enc.stdin.write(canvas.tobytes())
        if k % 200 == 0:
            el = time.monotonic() - t_start
            left = el / max(k, 1) * (nframes - k)
            print(f"\r   {style_name} video: frame {k} of {nframes}, about {dur(left)} left   ", end="", flush=True)
            emit("render", k / max(nframes, 1), paths.camera, f"{style} video")
    enc.stdin.close()
    enc.wait()
    # Sidecar for the app: maps time of day <-> video time (day_t = video_t / length * 86400).
    try:  # how the objects were found (recorded since v0.1.12)
        analysis = json.loads((paths.tubes / "analysis.json").read_text())
    except (OSError, ValueError):
        analysis = None
    dest.with_suffix(".json").write_text(json.dumps(
        {"length": length, "duration": total, "fps": fps, "speed": speed, "drift_min": drift_min, "tubes": len(tubes),
         "style": style, "color": color, "density": density, "max_tube": max_tube, "labels": labels,
         "categories": sorted({t.category for t in tubes}),
         "rendered": time.strftime("%Y-%m-%dT%H:%M:%S"), "analysis": analysis}))
    print(f"\r   {style_name} video done in {dur(time.monotonic() - t_start)}: {dest.name}" + " " * 30)
    return dest


# ---------------------------------------------------------------- stills

def _legend(canvas: np.ndarray) -> None:
    H, W = canvas.shape[:2]
    x0, x1, y = int(W * 0.25), int(W * 0.75), H - 14
    for x in range(x0, x1):
        cv2.line(canvas, (x, y - 6), (x, y + 6), time_bgr((x - x0) / (x1 - x0) * 86400), 1)
    for hr in range(0, 25, 3):
        x = x0 + int((x1 - x0) * hr / 24)
        cv2.putText(canvas, clock(hr * 3600 % 86400).replace(":00", ""), (x - 10, y - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)


def _scale(canvas: np.ndarray) -> float:
    return canvas.shape[1] / 1920 * 0.55


def _still(paths: DayPaths, name: str, kind: str) -> tuple[Path, str]:
    """Where a still goes (trails.jpg, or trails_person.jpg for one type) and its caption prefix."""
    if not kind:
        return paths.out / f"{name}.jpg", f"{paths.day:%a %b %-d}"
    words = "+".join(CATEGORY_NAME.get(k, k) for k in kind.split("-"))
    return paths.out / f"{name}_{kind}.jpg", f"{paths.day:%a %b %-d}  {words}:"


def render_trails(paths: DayPaths, tubes: list[Tube], bgs: Backgrounds, kind: str = "") -> Path:
    canvas = (bgs.daylight() * 0.55).astype(np.uint8)
    for t in sorted(tubes, key=lambda t: t.t0):
        pts = np.array([((x0 + x1) / 2, y1) for x0, y0, x1, y1 in t.boxes], np.float32)
        if len(pts) >= 5:
            k = np.ones(5) / 5
            pts = np.stack([np.convolve(pts[:, 0], k, "valid"), np.convolve(pts[:, 1], k, "valid")], 1)
        col = time_bgr(t.t0)
        cv2.polylines(canvas, [pts.astype(np.int32)], False, col, 2, cv2.LINE_AA)
        cv2.circle(canvas, tuple(pts[0].astype(int)), 3, col, -1, cv2.LINE_AA)
    dest, head = _still(paths, "trails", kind)
    chip(canvas, f"{head}  {len(tubes)} paths", _scale(canvas))
    _legend(canvas)
    cv2.imwrite(str(dest), canvas, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return dest


def render_strobe(paths: DayPaths, tubes: list[Tube], bgs: Backgrounds, kind: str = "", max_overlap: float = 0.5) -> Path:
    """One snapshot per object (its middle frame), far objects first, skipping ones that
    would mostly hide something already placed -- a 'everyone who came by' group shot."""
    canvas = bgs.daylight()
    H, W = canvas.shape[:2]
    day_lum = float(canvas.mean())
    covered = np.zeros((H, W), bool)
    picks = []
    for t in tubes:
        if float(bgs.at(t.t0)[::8, ::8].mean()) < 0.6 * day_lum:
            continue  # night/dusk cutouts look wrong pasted on a daytime scene
        i = len(t.times) // 2
        picks.append((t.boxes[i][3], t, i))
    placed = []
    for _, t, i in sorted(picks, key=lambda p: p[0]):  # by bottom edge: far (top) to near (bottom)
        crop = t.crop(i)
        x0, y0 = t.boxes[i][:2]
        h, w = min(crop.shape[0], H - y0), min(crop.shape[1], W - x0)
        m = crop[:h, :w, 3] > 127
        area = int(m.sum())
        if area == 0 or (covered[y0:y0 + h, x0:x0 + w] & m).sum() > max_overlap * area:
            continue
        composite(canvas, crop, t.boxes[i], 1.0)
        covered[y0:y0 + h, x0:x0 + w] |= m
        placed.append((t, i))
    scale = _scale(canvas)
    taken: list[tuple[int, int, int, int]] = []
    for t, i in reversed(placed):  # nearest objects get first claim on label space
        x0, y0 = t.boxes[i][:2]
        rect = (x0, y0 - 20, x0 + 52, y0)
        if any(r[0] < rect[2] and rect[0] < r[2] and r[1] < rect[3] and rect[1] < r[3] for r in taken):
            continue
        taken.append(rect)
        label(canvas, clock(t.t0), x0, y0 - 2, CATEGORY_BGR[t.category], scale * 0.85)
    dest, head = _still(paths, "strobe", kind)
    chip(canvas, f"{head}  {len(placed)} of {len(tubes)} (daylight)", scale)
    cv2.imwrite(str(dest), canvas, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return dest


def render_heatmap(paths: DayPaths, tubes: list[Tube], bgs: Backgrounds, kind: str = "") -> Path:
    """How many objects passed through each pixel (each object counts once, however long it stayed)."""
    base = bgs.daylight()
    H, W = base.shape[:2]
    heat = np.zeros((H, W), np.float32)
    for t in tubes:
        bx0 = min(b[0] for b in t.boxes); by0 = min(b[1] for b in t.boxes)
        bx1 = max(b[2] for b in t.boxes); by1 = max(b[3] for b in t.boxes)
        foot = np.zeros((by1 - by0, bx1 - bx0), np.uint8)
        for i in range(0, len(t.times), 2):
            a = t.crop(i)[:, :, 3]
            x0, y0 = t.boxes[i][0] - bx0, t.boxes[i][1] - by0
            h, w = min(a.shape[0], foot.shape[0] - y0), min(a.shape[1], foot.shape[1] - x0)
            np.maximum(foot[y0:y0 + h, x0:x0 + w], a[:h, :w], out=foot[y0:y0 + h, x0:x0 + w])
        h, w = min(foot.shape[0], H - by0), min(foot.shape[1], W - bx0)
        heat[by0:by0 + h, bx0:bx0 + w] += foot[:h, :w] > 127
    dest, head = _still(paths, "heatmap", kind)
    if heat.max() > 0:
        top = max(1.0, float(np.percentile(heat[heat > 0], 98)))
        norm = np.clip(heat / top, 0, 1)
        colored = cv2.applyColorMap((norm * 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
        a = np.where(heat > 0, 0.25 + 0.6 * norm, 0)[:, :, None]
        base = (base * 0.6 * (1 - a) + colored * a).astype(np.uint8)
        chip(base, f"{head}  brightest = {int(top)}+ objects", _scale(base))
    cv2.imwrite(str(dest), base, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return dest


CATEGORY_NAME = {"person": "people", "vehicle": "vehicles", "animal": "animals"}


def run(cameras, day, *, styles: list[str], color: str, length: float | None, density: float, speed: float,
        max_tube: float, fps: float, labels: bool, only: set[str] | None, stills: bool,
        video: bool = True, drift_min: float = 10.0, per_type: list[str] = ()) -> None:
    """Render each camera's videos and stills.

    only:     render just these object types (videos and stills named ..._<types>).
    per_type: after the full videos, also render one video per listed type, in the first style
              (fewer objects, so a shorter, cheaper render).
    stills:   trails, group shot and heatmap; a full render also makes them for each type present
              (each takes seconds), so Insights can show one type at a time.
    """
    for cam in cameras:
        paths = DayPaths(cam, day)
        print(f"== {cam}: making the videos and stills for {day}")
        all_tubes = load_tubes(paths, None)
        tubes = [t for t in all_tubes if not only or t.category in only]
        if not tubes:
            print("   no moving objects found for this day yet; run the analysis first" if not all_tubes
                  else f"   no {', '.join(CATEGORY_NAME.get(o, o) for o in sorted(only))} on this camera this day")
            continue
        kinds = [k for k in CATEGORY_NAME if any(t.category == k for t in all_tubes)]
        still_sets = ([("-".join(sorted(only)), tubes)] if only else
                      [("", tubes)] + [(k, [t for t in all_tubes if t.category == k]) for k in kinds]) if stills else []
        extras = [k for k in per_type if not only and video and any(t.category == k for t in all_tubes)]
        what = f"{len(tubes)} objects" + (f" ({', '.join(CATEGORY_NAME.get(o, o) for o in sorted(only))} only)" if only else "")
        print(f"   {what}, {len(styles) if video else 0} video style(s)"
              + (", plus trails, group shot and heatmap stills" if stills else "")
              + (f" (also for {', '.join(CATEGORY_NAME[k] for k in kinds)} separately)" if stills and not only and kinds else "")
              + (f", plus {', '.join(CATEGORY_NAME.get(k, k) + '-only' for k in extras)} video(s)" if extras else ""))
        bgs = Backgrounds(paths.backgrounds)
        suffix = f"_{'-'.join(sorted(only))}" if only else ""
        for style in styles if video else []:
            render_video(paths, tubes, bgs, style=style, color=color, length=length, density=density, speed=speed,
                         max_tube=max_tube, fps=fps, labels=labels, tag=f"{style}_{color}{suffix}",
                         drift_min=drift_min)
        for kind in extras:
            subset = [t for t in all_tubes if t.category == kind]
            print(f"   {CATEGORY_NAME.get(kind, kind)} only:")
            render_video(paths, subset, bgs, style=styles[0], color=color, length=length, density=density,
                         speed=speed, max_tube=max_tube, fps=fps, labels=labels,
                         tag=f"{styles[0]}_{color}_{kind}", drift_min=drift_min)
        if still_sets:
            paths.out.mkdir(parents=True, exist_ok=True)
        for kind, subset in still_sets:
            for fn in (render_trails, render_strobe, render_heatmap):
                print(f"   still image: {fn(paths, subset, bgs, kind).name}")
        publish(paths)


def publish(paths: DayPaths) -> None:
    """Copy rendered outputs from the local work area to the share (no-op when they're the same)."""
    if paths.out.resolve() == paths.published.resolve() or not paths.out.exists():
        return
    paths.published.mkdir(parents=True, exist_ok=True)

    def same(f: Path) -> bool:  # copy2 keeps mtimes, so an unchanged file matches exactly
        d = paths.published / f.name
        try:
            a, b = f.stat(), d.stat()
        except FileNotFoundError:
            return False
        return a.st_size == b.st_size and int(a.st_mtime) == int(b.st_mtime)

    files = [f for f in sorted(paths.out.iterdir()) if f.is_file() and not same(f)]
    if not files:
        print("   the share is already up to date")
        return
    print(f"   copying {len(files)} new or changed files to the share ({paths.published})…")
    for f in files:
        shutil.copy2(f, paths.published / f.name)
    print("   done")
