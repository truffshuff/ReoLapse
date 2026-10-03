"""A small per-day index of every tube (object): its time span and a sampled path of its box.

Written by analysis next to the tubes; built once from the tube files for days analyzed earlier.
Used to answer "what passed through this part of the picture?" without loading the crops.
"""

from __future__ import annotations

import json
import math
import pickle
import shutil
from pathlib import Path

INDEX_NAME = "index.json"
SAMPLE_EVERY = 0.4  # seconds between stored box samples


def tube_meta(name: str, t0: float, t1: float, category: str, boxes: list[tuple]) -> dict:
    """boxes: [(t, x0, y0, x1, y1), ...] in frame pixels."""
    path, last = [], -1e9
    for t, x0, y0, x1, y1 in boxes:
        if t - last >= SAMPLE_EVERY or (t, x0, y0, x1, y1) == boxes[-1]:
            path.append([round(t, 2), int(x0), int(y0), int(x1), int(y1)])
            last = t
    return {"id": Path(name).stem, "t0": round(t0, 2), "t1": round(t1, 2), "category": category, "path": path}


def write(tubes_dir: Path, metas: list[dict], size: tuple[int, int]) -> None:
    data = {"width": size[0], "height": size[1], "tubes": sorted(metas, key=lambda m: m["t0"])}
    tmp = tubes_dir / (INDEX_NAME + ".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")))
    tmp.replace(tubes_dir / INDEX_NAME)


def load(tubes_dir: Path, frame_size: tuple[int, int] | None = None) -> dict | None:
    """The index, building (and caching) it from the tube files if it's missing."""
    f = tubes_dir / INDEX_NAME
    if f.exists():
        return json.loads(f.read_text())
    if not tubes_dir.is_dir():
        return None
    metas = []
    for p in sorted(tubes_dir.glob("*.pkl")):
        with open(p, "rb") as fh:
            d = pickle.load(fh)
        metas.append(tube_meta(p.name, d["t0"], d["t1"], d["category"], [fr[:5] for fr in d["frames"]]))
    size = frame_size or (0, 0)
    try:
        write(tubes_dir, metas, size)
    except OSError:
        pass  # read-only location: just don't cache
    return {"width": size[0], "height": size[1], "tubes": sorted(metas, key=lambda m: m["t0"])}


def query(index: dict, regions: list[tuple[float, float, float, float]], categories: set[str] | None = None) -> list[dict]:
    """Tubes whose box centre entered any region (regions normalised 0..1 as x0, y0, x1, y1).

    Returns each match once, with `enter`: the first time its centre was inside a region.
    """
    W, H = index["width"] or 1, index["height"] or 1
    out = []
    for m in index["tubes"]:
        if categories and m["category"] not in categories:
            continue
        for t, x0, y0, x1, y1 in m["path"]:
            cx, cy = (x0 + x1) / 2 / W, (y0 + y1) / 2 / H
            if any(rx0 <= cx <= rx1 and ry0 <= cy <= ry1 for rx0, ry0, rx1, ry1 in regions):
                out.append({"id": m["id"], "category": m["category"], "t0": m["t0"], "t1": m["t1"], "enter": t})
                break
    return out


# A vehicle must really travel, relative to its own size, to count as moving. Parked cars
# "wobble": the detector's box shifts a little frame to frame, which briefly fooled the
# short-window moving test (on Oct 1 ~600 of 899 "vehicles" were parked cars; with the medium
# model on Clear footage ~1,500 of 1,750). Cars that drive in or out cover far more than half
# their length. People and animals aren't filtered: those that barely travel are real (someone
# gardening, gesturing, half in view), checked on Oct 1.
MIN_TRAVEL = {"vehicle": 0.5}
PRUNED = "_pruned"


def travel_ratio(boxes: list) -> float:
    """Furthest the box centre gets from its (smoothed) start, in units of the box's median diagonal.
    boxes: [(x0, y0, x1, y1), ...] in time order."""
    if not boxes:
        return 0.0
    centres = [((b[0] + b[2]) / 2, (b[1] + b[3]) / 2) for b in boxes]
    k = max(1, len(centres) // 5)  # median of the first fifth: one jittery frame can't fake a start
    xs = sorted(c[0] for c in centres[:k])
    ys = sorted(c[1] for c in centres[:k])
    start = (xs[len(xs) // 2], ys[len(ys) // 2])
    diags = sorted(math.hypot(b[2] - b[0], b[3] - b[1]) for b in boxes)
    return max(math.dist(start, c) for c in centres) / max(diags[len(diags) // 2], 1.0)


def really_moved(category: str, boxes: list) -> bool:
    need = MIN_TRAVEL.get(category, 0.0)
    return need <= 0 or travel_ratio(boxes) >= need


def meta_moved(m: dict) -> bool:
    return really_moved(m["category"], [p[1:5] for p in m["path"]])


def prune(tubes_dir: Path, metas: list[dict] | None = None, frame_size: tuple[int, int] | None = None) -> tuple[list[dict], int]:
    """Move tubes that never really travelled into tubes/_pruned/ (reversible) and rewrite the index.
    Returns (kept metas, number pruned)."""
    if metas is None:
        index = load(tubes_dir, frame_size)
        if index is None:
            return [], 0
        metas, frame_size = index["tubes"], (index["width"], index["height"])
    kept, gone = [], []
    for m in metas:
        (kept if meta_moved(m) else gone).append(m)
    if gone:
        (tubes_dir / PRUNED).mkdir(exist_ok=True)
        for m in gone:
            f = tubes_dir / f"{m['id']}.pkl"
            if f.exists():
                shutil.move(str(f), tubes_dir / PRUNED / f.name)
    if frame_size:
        write(tubes_dir, kept, frame_size)
    return kept, len(gone)
