"""Make a synthetic demo archive for screenshots and videos in the docs (no real camera footage).

    uv run python tools/make_demo.py demo        # -> demo/archive, demo/work, demo/settings.json

Three made-up cameras (Driveway, Front Yard, Back Yard) watch painted scenes that go from day to
night. Drawn people, cars, dogs and cats come and go on a believable daily schedule. Everything the
analysis would produce is written in Reolapse's real formats (object tubes, index, backgrounds,
analysis.json), for a week of days; the main day also gets an hour of "recorded" footage per camera
(4-5 PM) and is rendered with the real renderer. Deterministic: the same seed gives the same demo.
"""

from __future__ import annotations

import json
import math
import pickle
import random
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

W, H = 1280, 720
FPS = 5.0  # object samples per second (as an analysis at 5 fps)
RAW_FPS = 10
MAIN_DAY = date(2026, 10, 12)
DAYS = [MAIN_DAY - timedelta(days=n) for n in range(6, -1, -1)]
CAMERAS = ["Driveway", "Front Yard", "Back Yard"]
RAW_HOUR = 16  # the hour of recorded footage on the main day


def slug(name: str) -> str:
    return name.lower().replace(" ", "_")


def daylight(t: float) -> float:
    """0 at night .. 1 at midday, with dawn and dusk."""
    h = t / 3600
    return float(np.clip((math.sin((h - 6.5) / 12.5 * math.pi) + 0.15) / 0.9, 0, 1)) ** 0.7


# ---------------------------------------------------------------- scenes

def _noise(shape, rng, scale=6):
    n = rng.normal(0, scale, shape[:2]).astype(np.float32)
    return cv2.GaussianBlur(n, (0, 0), 1.2)[..., None]


def _house(img, x, y, w, h, wall, roof, lights, night, rng):
    cv2.rectangle(img, (x, y), (x + w, y + h), wall, -1)
    cv2.fillPoly(img, [np.array([(x - 8, y), (x + w // 2, y - h // 2), (x + w + 8, y)])], roof)
    for i in range(3):
        wx = x + 12 + i * (w - 24) // 3
        col = (120, 210, 250) if (night and lights[i]) else (95, 90, 80)
        cv2.rectangle(img, (wx, y + h // 4), (wx + (w - 48) // 3, y + h // 4 + h // 5), col, -1)
    cv2.rectangle(img, (x + w // 2 - 9, y + h - 34), (x + w // 2 + 9, y + h), (60, 70, 95), -1)


def paint_scene(camera: str, night: bool, rng: random.Random) -> np.ndarray:
    nr = np.random.default_rng(7)
    img = np.zeros((H, W, 3), np.uint8)
    sky = np.linspace(0, 1, 220)[:, None, None]
    top, bot = ((70, 40, 30), (110, 70, 50)) if night else ((235, 200, 140), (250, 230, 205))
    img[:220] = (np.array(top) * (1 - sky) + np.array(bot) * sky).astype(np.uint8)
    lights = [[rng.random() < 0.5 for _ in range(3)] for _ in range(6)]
    grass = (40, 75, 45) if night else (70, 165, 95)
    if camera == "Back Yard":
        img[160:] = grass
        cv2.rectangle(img, (0, 150), (W, 260), (55, 75, 95) if night else (150, 190, 215), -1)  # fence
        for x in range(0, W, 26):
            cv2.line(img, (x, 150), (x, 260), (40, 55, 70) if night else (120, 160, 185), 2)
        for cx, r in ((180, 90), (1100, 110)):  # trees
            cv2.circle(img, (cx, 150), r, (30, 55, 35) if night else (55, 120, 60), -1)
            cv2.rectangle(img, (cx - 10, 150), (cx + 10, 300), (40, 50, 70), -1)
        cv2.fillPoly(img, [np.array([(0, 560), (W, 520), (W, H), (0, H)])], (70, 80, 95) if night else (150, 165, 190))  # patio
        for x in range(0, W, 80):
            cv2.line(img, (x, 540 + x // 30), (x - 60, H), (60, 70, 85) if night else (130, 145, 170), 1)
    else:
        for i, x in enumerate(range(-40, W, 230)):  # houses across the street
            _house(img, x, 120, 190, 100, (90, 95, 110) if night else (205, 215, 225),
                   (55, 55, 75) if night else (110, 110, 140), lights[i % 6], night, rng)
        road_y = 255 if camera == "Driveway" else 330
        img[220:road_y - 40] = grass
        cv2.rectangle(img, (0, road_y - 40), (W, road_y - 28), (150, 155, 160) if not night else (80, 82, 88), -1)
        cv2.rectangle(img, (0, road_y - 28), (W, road_y + 40), (60, 62, 66) if night else (105, 108, 112), -1)
        for x in range(0, W, 90):
            cv2.line(img, (x, road_y + 6), (x + 45, road_y + 6), (150, 200, 220) if not night else (90, 120, 130), 3)
        cv2.rectangle(img, (0, road_y + 40), (W, road_y + 70), (160, 165, 170) if not night else (85, 88, 95), -1)  # sidewalk
        img[road_y + 70:] = grass
        if camera == "Driveway":
            cv2.fillPoly(img, [np.array([(560, road_y + 40), (760, road_y + 40), (980, H), (380, H)])],
                         (70, 72, 76) if night else (120, 122, 126))
            cv2.rectangle(img, (1150, 200), (1220, H), (90, 95, 120) if night else (190, 195, 215), -1)  # porch post
            cv2.rectangle(img, (1220, 200), (W, H), (60, 70, 95) if night else (150, 160, 185), -1)
    img = np.clip(img.astype(np.float32) + _noise(img.shape, nr), 0, 255).astype(np.uint8)
    lamp = (W // 2 - 300, 200)
    cv2.line(img, lamp, (lamp[0], lamp[1] + 90), (60, 60, 60) if night else (90, 90, 90), 4)  # a street lamp
    cv2.circle(img, lamp, 7, (180, 240, 255) if night else (200, 200, 200), -1)
    if night:
        glow = np.zeros((H, W), np.float32)
        cv2.circle(glow, (lamp[0], lamp[1] + 60), 70, 1.0, -1)
        glow = cv2.GaussianBlur(glow, (0, 0), 40)[..., None]
        img = np.clip(img + glow * np.array([30, 80, 120]), 0, 255).astype(np.uint8)
    return img


class Scene:
    def __init__(self, camera: str):
        rng = random.Random(slug(camera))
        self.day = paint_scene(camera, False, rng)
        self.night = paint_scene(camera, True, random.Random(slug(camera)))

    def at(self, t: float) -> np.ndarray:
        f = daylight(t)
        return cv2.addWeighted(self.day, f, self.night, 1 - f, 0)


# ---------------------------------------------------------------- objects

PALETTE = [(60, 60, 200), (200, 120, 40), (40, 150, 60), (180, 180, 180), (40, 40, 40), (30, 120, 220),
           (150, 60, 140), (230, 230, 230), (80, 80, 160)]


@dataclass
class Thing:
    category: str  # person / vehicle / animal
    kind: str  # person / car / dog / cat
    t0: float
    dur: float
    path: list  # [(fraction 0..1, x, y)] waypoints at the feet / wheels
    color: tuple
    color2: tuple
    size: float  # height in px at scale 1

    def pos(self, t: float):
        u = (t - self.t0) / self.dur
        for (u0, x0, y0), (u1, x1, y1) in zip(self.path, self.path[1:]):
            if u0 <= u <= u1:
                k = (u - u0) / max(u1 - u0, 1e-6)
                return x0 + (x1 - x0) * k, y0 + (y1 - y0) * k, math.copysign(1, (x1 - x0) or 1)
        return self.path[-1][1], self.path[-1][2], 1.0

    def sprite(self, t: float):
        """(x0, y0, x1, y1), BGRA crop at time t."""
        x, y, facing = self.pos(t)
        s = (0.4 + 0.6 * (y - 200) / (H - 200)) * self.size
        light = 0.35 + 0.65 * daylight(t)
        phase = (t - self.t0) * (2.2 if self.kind == "person" else 4.0)
        if self.kind == "car":
            w, h = int(s * 2.4), int(s)
        elif self.kind in ("dog", "cat"):
            w, h = int(s * 1.5), int(s)
        else:
            w, h = max(8, int(s * 0.42)), int(s)
        pad = 4
        img = np.zeros((h + 2 * pad, w + 2 * pad, 4), np.uint8)
        c = tuple(int(v * light) for v in self.color)
        c2 = tuple(int(v * light) for v in self.color2)
        skin = tuple(int(v * light) for v in (120, 160, 205))
        def fill(draw, col):
            layer = np.zeros(img.shape[:2], np.uint8)
            draw(layer)
            img[layer > 0, :3] = col
            img[layer > 0, 3] = 255
        P = lambda a, b: (int(a) + pad, int(b) + pad)
        if self.kind == "person":
            sw = math.sin(phase * math.pi) * w * 0.35
            fill(lambda m: cv2.line(m, P(w / 2, h * 0.58), P(w / 2 - sw, h - 1), 255, max(2, int(w * 0.28))), c2)
            fill(lambda m: cv2.line(m, P(w / 2, h * 0.58), P(w / 2 + sw, h - 1), 255, max(2, int(w * 0.28))), c2)
            fill(lambda m: cv2.rectangle(m, P(w * 0.12, h * 0.2), P(w * 0.88, h * 0.62), 255, -1), c)
            fill(lambda m: cv2.line(m, P(w * 0.15, h * 0.24), P(w * 0.15 + sw * 0.6, h * 0.55), 255, max(2, int(w * 0.18))), c)
            fill(lambda m: cv2.circle(m, P(w / 2, h * 0.1), max(3, int(w * 0.3)), 255, -1), skin)
        elif self.kind == "car":
            fill(lambda m: cv2.rectangle(m, P(0, h * 0.35), P(w - 1, h * 0.85), 255, -1), c)
            fill(lambda m: cv2.fillPoly(m, [np.array([P(w * 0.22, h * 0.36), P(w * 0.34, h * 0.05), P(w * 0.7, h * 0.05), P(w * 0.82, h * 0.36)])], 255), c)
            glass = tuple(int(v * light) for v in (90, 70, 60))
            fill(lambda m: cv2.fillPoly(m, [np.array([P(w * 0.28, h * 0.34), P(w * 0.37, h * 0.12), P(w * 0.67, h * 0.12), P(w * 0.76, h * 0.34)])], 255), glass)
            for cx in (0.22, 0.78):
                fill(lambda m, cx=cx: cv2.circle(m, P(w * cx, h * 0.84), max(3, int(h * 0.17)), 255, -1), (30, 30, 30))
            if daylight(t) < 0.4:  # headlights
                hx = w - 3 if facing > 0 else 2
                fill(lambda m: cv2.circle(m, P(hx, h * 0.55), max(2, int(h * 0.1)), 255, -1), (180, 245, 255))
        else:
            fill(lambda m: cv2.ellipse(m, P(w * 0.48, h * 0.48), (int(w * 0.36), int(h * 0.2)), 0, 0, 360, 255, -1), c)
            hx = w * 0.85 if facing > 0 else w * 0.15
            fill(lambda m: cv2.circle(m, P(hx, h * 0.3), max(3, int(h * 0.18)), 255, -1), c)
            sw = math.sin(phase * math.pi) * w * 0.08
            for lx in (0.25, 0.7):
                fill(lambda m, lx=lx: cv2.line(m, P(w * lx, h * 0.55), P(w * lx + sw, h - 1), 255, max(2, int(h * 0.1))), c2)
            tx = w * 0.08 if facing > 0 else w * 0.92
            fill(lambda m: cv2.line(m, P(w * 0.5 - (w * 0.38 if facing > 0 else -w * 0.38), h * 0.4), P(tx, h * 0.15), 255, max(2, int(h * 0.07))), c)
        if facing < 0 and self.kind != "person":
            img = img[:, ::-1].copy()
        x0, y0 = int(x - img.shape[1] / 2), int(y - img.shape[0])
        cx0, cy0 = max(0, x0), max(0, y0)
        cx1, cy1 = min(W, x0 + img.shape[1]), min(H, y0 + img.shape[0])
        if cx1 - cx0 < 4 or cy1 - cy0 < 4:
            return None, None  # off screen
        return (cx0, cy0, cx1, cy1), img[cy0 - y0:cy1 - y0, cx0 - x0:cx1 - x0]


def schedule(camera: str, day: date, rng: random.Random) -> list[Thing]:
    """A day's comings and goings for one camera."""
    weekend = day.weekday() >= 5
    things: list[Thing] = []
    road_y = 255 if camera == "Driveway" else 330  # as painted
    walk_y = road_y + 62  # feet on the sidewalk

    def rate(h):  # relative activity by hour
        base = [0.15, 0.1, 0.05, 0.05, 0.1, 0.3, 0.8, 1.6, 1.8, 1.0, 0.9, 1.0, 1.2, 1.0, 1.0, 1.4, 1.9, 2.4, 2.2, 1.6,
                1.1, 0.8, 0.5, 0.3][h]
        return base * (0.8 if weekend and h in (7, 8, 17) else 1.3 if weekend and 10 <= h <= 16 else 1.0)

    def times(per_hour):
        out = []
        for h in range(24):
            n = np.random.default_rng(rng.randrange(1 << 30)).poisson(per_hour * rate(h))
            out += [h * 3600 + rng.random() * 3600 for _ in range(n)]
        return sorted(out)

    if camera != "Back Yard":
        for t in times(5.5):  # cars along the street
            left = rng.random() < 0.5
            y = road_y + (18 if left else -6)
            things.append(Thing("vehicle", "car", t, rng.uniform(4, 7), [(0, -150, y), (1, W + 150, y)] if left
                                else [(0, W + 150, y), (1, -150, y)], rng.choice(PALETTE), (0, 0, 0), rng.uniform(70, 95)))
        for t in times(2.2):  # people on the sidewalk, some with a dog
            left = rng.random() < 0.5
            dur = rng.uniform(14, 26)
            path = [(0, -40, walk_y), (1, W + 40, walk_y)] if left else [(0, W + 40, walk_y), (1, -40, walk_y)]
            things.append(Thing("person", "person", t, dur, path, rng.choice(PALETTE), rng.choice(PALETTE[3:6]), rng.uniform(150, 175)))
            if rng.random() < 0.25:
                d = 50 if left else -50
                things.append(Thing("animal", "dog", t + 0.4, dur, [(u, x + d, y + 6) for u, x, y in path],
                                    rng.choice([(40, 80, 140), (30, 30, 30), (150, 190, 220)]), (40, 40, 40), rng.uniform(55, 70)))
    if camera == "Driveway":
        for t in times(0.25):  # someone walks out to the car and drives off
            things.append(Thing("person", "person", t, rng.uniform(10, 16), [(0, 1120, 640), (0.6, 820, 560), (1, 700, 420)],
                                rng.choice(PALETTE), rng.choice(PALETTE[3:6]), 230))
            things.append(Thing("vehicle", "car", t + 25, rng.uniform(8, 11), [(0, 680, 600), (0.6, 640, 330), (1, -100, 305)],
                                rng.choice(PALETTE), (0, 0, 0), 120))
    if camera == "Back Yard":
        for t in times(1.3):
            things.append(Thing("person", "person", t, rng.uniform(12, 30), [(0, 1150, 690), (0.5, 640, 520), (1, 300, 470)] if rng.random() < 0.5
                                else [(0, 300, 470), (0.5, 700, 540), (1, 1150, 690)], rng.choice(PALETTE), rng.choice(PALETTE[3:6]), 230))
        for t in times(0.9):
            kind = rng.choice(["cat", "cat", "dog"])
            x0, x1 = rng.choice([(-60, W + 60), (W + 60, -60)])
            y = rng.uniform(320, 470)
            things.append(Thing("animal", kind, t, rng.uniform(8, 18), [(0, x0, y), (1, x1, y + rng.uniform(-40, 40))],
                                rng.choice([(40, 80, 140), (30, 30, 30), (200, 200, 200), (90, 120, 160)]), (40, 40, 40),
                                rng.uniform(45, 70) if kind == "cat" else rng.uniform(70, 90)))
    return [x for x in things if x.t0 + x.dur < 86400]


# ---------------------------------------------------------------- writing

def write_day(root: Path, work: Path, camera: str, day: date, scene: Scene, bg_from: Path | None) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from reolapse import tube_index

    rng = random.Random(f"{camera}{day}")
    d_work = work / slug(camera) / day.isoformat()
    d_root = root / slug(camera) / day.isoformat()
    tubes, bg = d_work / "tubes", d_work / "bg"
    shutil.rmtree(d_work, ignore_errors=True)
    tubes.mkdir(parents=True)
    d_root.mkdir(parents=True, exist_ok=True)
    if bg_from:
        shutil.copytree(bg_from, bg)
    else:
        bg.mkdir()
        for slot in range(144):
            cv2.imwrite(str(bg / f"{slot:03d}.jpg"), scene.at(slot * 600 + 300), [cv2.IMWRITE_JPEG_QUALITY, 90])
        (bg / ".complete").write_text(json.dumps({"size": [W, H]}))
    metas = []
    for i, th in enumerate(schedule(camera, day, rng)):
        frames = []
        for k in range(int(th.dur * FPS)):
            t = th.t0 + k / FPS
            box, img = th.sprite(t)
            if box is not None:
                frames.append((t, *box, cv2.imencode(".png", img)[1].tobytes()))
        if len(frames) < 5:
            continue
        name = f"{th.t0:08.1f}_{th.category}.pkl"
        n = 1
        while (tubes / name).exists():
            n += 1
            name = f"{th.t0:08.1f}_{th.category}_{n}.pkl"
        (tubes / name).write_bytes(pickle.dumps({"t0": frames[0][0], "t1": frames[-1][0], "category": th.category,
                                                 "cls": th.kind if th.kind != "car" else "car", "fps": FPS,
                                                 "frames": frames, "context": []}))
        metas.append(tube_index.tube_meta(name, frames[0][0], frames[-1][0], th.category, [f[:5] for f in frames]))
    tube_index.write(tubes, metas, (W, H))
    (tubes / "analysis.json").write_text(json.dumps({
        "stream": "main", "source_size": [W, H], "size": [W, H], "fps": FPS, "model": "yolo26l-seg.pt", "imgsz": 1280,
        "types": ["all"], "hours": [0, 24], "finished": f"{day + timedelta(days=1)}T03:10:00",
        "finished_ts": datetime.combine(day + timedelta(days=1), datetime.min.time()).timestamp() + 11400, "app": "demo"}, indent=1))
    raw = d_root / "raw"
    raw.mkdir(exist_ok=True)
    files = []
    if day == MAIN_DAY:
        name = f"main_{day:%Y%m%d}_{RAW_HOUR:02d}0000.mp4"
        files.append({"path": name, "start": f"{day}T{RAW_HOUR:02d}:00:00", "end": f"{day}T{RAW_HOUR + 1:02d}:00:00", "stream": "main"})
        record(raw / name, scene, schedule(camera, day, random.Random(f"{camera}{day}")), RAW_HOUR * 3600, 3600)
    (d_root / "files.json").write_text(json.dumps(files, indent=1))
    (d_root / "events.json").write_text("[]")
    return len(metas)


def record(dest: Path, scene: Scene, things: list[Thing], start: float, seconds: float) -> None:
    """An hour of 'recorded' footage: the scene with every object drawn where it is."""
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
                            "-r", str(RAW_FPS), "-i", "-", "-c:v", "h264_videotoolbox", "-b:v", "3M", "-pix_fmt", "yuv420p",
                            "-movflags", "+faststart", str(dest)], stdin=subprocess.PIPE)
    active = sorted((x for x in things if x.t0 < start + seconds and x.t0 + x.dur > start), key=lambda x: x.t0)
    for k in range(int(seconds * RAW_FPS)):
        t = start + k / RAW_FPS
        frame = scene.at(t)
        for th in active:
            if th.t0 <= t <= th.t0 + th.dur:
                box, img = th.sprite(t)
                if box is None:
                    continue
                x0, y0, x1, y1 = box
                a = img[:, :, 3:4].astype(np.float32) / 255
                region = frame[y0:y1, x0:x1]
                region[:] = (region * (1 - a) + img[:, :, :3] * a).astype(np.uint8)
        cv2.putText(frame, f"{datetime.combine(MAIN_DAY, datetime.min.time()) + timedelta(seconds=t):%m/%d/%Y %H:%M:%S}",
                    (16, H - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (240, 240, 240), 2, cv2.LINE_AA)
        enc.stdin.write(frame.tobytes())
    enc.stdin.close()
    enc.wait()


def main(out: str) -> None:
    out = Path(out)
    root, work = out / "archive", out / "work"
    settings = {
        "cameras": CAMERAS, "streams": {c: "main" for c in CAMERAS},
        "analysis": {"fps": 5.0, "types": "all", "model": "yolo26l-seg.pt", "imgsz": 1280},
        "render": {"styles": ["cutout", "ghost", "outline"], "color": "category", "drift": 2.0, "speed": 2.0, "density": 4.0,
                   "max_tube": 20.0, "per_type": ["person"], "clock": "adaptive", "second_clock": True},
        "retention": {"raw_days": 14, "results_days": 90, "auto": True},
        "nightly": {"enabled": True, "time": "02:00", "catch_up_days": 3},
        "storage": {"work_root": "", "data_root": ""},
        "nvr": {"host": "192.168.1.20", "user": "reolapse", "port": 80},
        "updates": {"repo": "truffshuff/ReoLapse", "check_on_launch": False},
        "areas": {"Driveway": [{"name": "Driveway", "boxes": [{"x0": 0.32, "y0": 0.5, "x1": 0.74, "y1": 1.0}]}],
                  "Front Yard": [{"name": "Sidewalk", "boxes": [{"x0": 0.0, "y0": 0.42, "x1": 1.0, "y1": 0.58}]}]},
        "ignore": {"Back Yard": [{"x0": 0.07, "y0": 0.05, "x1": 0.21, "y1": 0.36}]},
        "camera_analysis": {"Front Yard": {"imgsz": 960, "fps": 5.0}},
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "settings.json").write_text(json.dumps(settings, indent=2))
    for camera in CAMERAS:
        scene = Scene(camera)
        first_bg = None
        for day in reversed(DAYS):  # main day first: its backgrounds are reused for the others
            n = write_day(root, work, camera, day, scene, first_bg)
            first_bg = first_bg or work / slug(camera) / day.isoformat() / "bg"
            print(f"{camera} {day}: {n} objects")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "demo")
