"""GUI-editable settings, stored as JSON. NVR credentials stay in .env, never here."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

SETTINGS_PATH = Path(
    os.environ.get("REOLAPSE_SETTINGS", "~/Library/Application Support/reolapse/settings.json")
).expanduser()

DEFAULTS: dict = {
    "cameras": ["Garage", "Porch Left", "Back Yard Right"],
    "analysis": {"fps": 5.0, "types": "all", "model": "yolo26n-seg.pt", "imgsz": 1280},
    "render": {"styles": ["cutout"], "color": "category", "drift": 10.0, "speed": 2.0, "density": 6.0,
               "max_tube": 20.0, "per_type": []},  # per_type: extra videos of only people/vehicles/animals
    "retention": {"raw_days": 14, "results_days": 90},
    "nightly": {"enabled": False, "time": "02:00"},
    "storage": {"work_root": "", "data_root": ""},  # empty = defaults (REOLAPSE_WORK / REOLAPSE_DATA override)
    "nvr": {"host": "", "user": "", "port": 80},  # the password lives in the system keychain, never here
    "updates": {"repo": "truffshuff/ReoLapse", "check_on_launch": True},  # token: system keychain
    "streams": {},  # camera name -> "sub" (Fluent, default) or "main" (Clear)
    "areas": {},  # camera name -> [{"name": "Driveway", "boxes": [{"x0": .., "y0": .., "x1": .., "y1": ..}]}]
}

# Settings whose keys are user data (camera names), not a fixed schema.
FREE_FORM = {"streams", "areas"}


def _check_areas(value: dict) -> None:
    """Named areas per camera for reports: boxes in 0..1 picture coordinates."""
    for camera, areas in value.items():
        if not isinstance(areas, list) or len(areas) > 20:
            raise ValueError(f"areas for {camera} must be a list of at most 20")
        for a in areas:
            name, boxes = a.get("name") if isinstance(a, dict) else None, a.get("boxes") if isinstance(a, dict) else None
            if not isinstance(name, str) or not name.strip() or len(name) > 60:
                raise ValueError("each area needs a name of at most 60 characters")
            if not isinstance(boxes, list) or not 1 <= len(boxes) <= 20:
                raise ValueError(f"area {name!r} needs 1 to 20 boxes")
            for b in boxes:
                v = [b.get(k) for k in ("x0", "y0", "x1", "y1")] if isinstance(b, dict) else []
                if len(v) != 4 or not all(isinstance(x, (int, float)) and 0 <= x <= 1 for x in v):
                    raise ValueError(f"area {name!r} has a box outside the picture")


def _merge(base: dict, patch: dict, path: str = "") -> dict:
    """Overlay `patch` onto `base`, rejecting keys the defaults don't know."""
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if key not in base:
            raise ValueError(f"unknown setting {path}{key}")
        if not path and key in FREE_FORM:
            if not isinstance(value, dict):
                raise ValueError(f"setting {key} must be an object")
            if key == "streams" and any(v not in ("sub", "main") for v in value.values()):
                raise ValueError("streams values must be 'sub' (Fluent) or 'main' (Clear)")
            if key == "areas":
                _check_areas(value)
            out[key] = dict(value)
        elif isinstance(base[key], dict):
            if not isinstance(value, dict):
                raise ValueError(f"setting {path}{key} must be an object")
            out[key] = _merge(base[key], value, f"{path}{key}.")
        else:
            out[key] = value
    return out


def load(path: Path | None = None) -> dict:
    path = path or SETTINGS_PATH
    if not path.exists():
        return copy.deepcopy(DEFAULTS)
    return _merge(DEFAULTS, json.loads(path.read_text()))


def save(patch: dict, path: str | Path | None = None) -> dict:
    """Merge `patch` into the stored settings and write them atomically. Returns the result."""
    path = Path(path) if path else SETTINGS_PATH
    merged = _merge(load(path), patch)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(merged, indent=2))
    tmp.replace(path)
    return merged
