"""What the person reviewing a day has decided, applied wherever objects are read.

- Corrections: an object's type fixed by hand ("person" / "vehicle" / "animal"), or "hidden" for
  something that isn't an object at all. Kept per camera-day next to the objects
  (tubes/corrections.json), so they're lost only if the day is analyzed again (which makes new objects).
- Ignore zones: parts of a camera's picture where nothing should count (a waving flag, a tree, the
  street). Settings "ignore": {camera: [{"x0", "y0", "x1", "y1"}, ...]} in 0..1 picture coordinates.
  An object that spends at least IGNORE_SHARE of its time inside them is left out. The zones also
  mask the motion gate in later scans, so movement there doesn't wake the detector.

Counts, charts, area searches, reports and renders all go through `apply` / `keep`, so a correction
shows up everywhere (videos at their next render).
"""

from __future__ import annotations

import json
from pathlib import Path

CORRECTIONS = "corrections.json"
VALUES = {"person", "vehicle", "animal", "hidden"}
IGNORE_SHARE = 0.9


def load_corrections(tubes_dir: Path) -> dict[str, str]:
    try:
        data = json.loads((tubes_dir / CORRECTIONS).read_text())
        return {k: v for k, v in data.items() if v in VALUES}
    except (OSError, ValueError):
        return {}


def save_correction(tubes_dir: Path, obj_id: str, value: str | None) -> dict[str, str]:
    """Set (or with None, clear) the correction for one object; returns all of the day's."""
    if value is not None and value not in VALUES:
        raise ValueError(f"a correction is one of {sorted(VALUES)}")
    data = load_corrections(tubes_dir)
    if value is None:
        data.pop(obj_id, None)
    else:
        data[obj_id] = value
    tmp = tubes_dir / (CORRECTIONS + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    tmp.replace(tubes_dir / CORRECTIONS)
    return data


def zones_for(camera: str, settings: dict | None = None) -> list[dict]:
    if settings is None:
        from .settings import load

        settings = load()
    return list(settings.get("ignore", {}).get(camera, []))


def in_zones(cx: float, cy: float, zones: list[dict]) -> bool:
    return any(z["x0"] <= cx <= z["x1"] and z["y0"] <= cy <= z["y1"] for z in zones)


def ignored(path: list, zones: list[dict], width: float, height: float) -> bool:
    """path: [(t, x0, y0, x1, y1), ...] in pixels; True if the object stayed in the zones."""
    if not zones or not path or not width or not height:
        return False
    inside = sum(in_zones((p[1] + p[3]) / 2 / width, (p[2] + p[4]) / 2 / height, zones) for p in path)
    return inside >= IGNORE_SHARE * len(path)


def apply(index: dict, corrections: dict[str, str], zones: list[dict]) -> dict:
    """The day's object index with corrections applied and hidden / zone-ignored objects removed.
    Each object gets "corrected": True when its type was fixed by hand."""
    W, H = index.get("width") or 0, index.get("height") or 0
    tubes = []
    for m in index["tubes"]:
        fix = corrections.get(m["id"])
        if fix == "hidden":
            continue
        if fix and not ignored(m["path"], zones, W, H):
            tubes.append({**m, "category": fix, "corrected": True})
        elif not fix and not ignored(m["path"], zones, W, H):
            tubes.append(m)
    return {**index, "tubes": tubes}


def keep(obj_id: str, boxes: list, corrections: dict[str, str], zones: list[dict], width: float, height: float):
    """For one object while rendering: its corrected category, or None to leave it out.
    boxes: [(x0, y0, x1, y1), ...] in pixels."""
    fix = corrections.get(obj_id)
    if fix == "hidden" or ignored([(0, *b) for b in boxes], zones, width, height):
        return None
    return fix or ""


def gate_mask(zones: list[dict], width: int, height: int):
    """A uint8 mask (1 = watch, 0 = ignore) for the motion gate at its working size."""
    import numpy as np

    mask = np.ones((height, width), np.uint8)
    for z in zones:
        mask[int(z["y0"] * height):int(round(z["y1"] * height)), int(z["x0"] * width):int(round(z["x1"] * width))] = 0
    return mask
