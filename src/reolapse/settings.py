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
               "max_tube": 20.0},
    "retention": {"raw_days": 14, "results_days": 90},
    "nightly": {"enabled": False, "time": "02:00"},
    "storage": {"work_root": ""},  # empty = use the share (REOLAPSE_WORK in .env overrides)
    "streams": {},  # camera name -> "sub" (Fluent, default) or "main" (Clear)
}

# Settings whose keys are user data (camera names), not a fixed schema.
FREE_FORM = {"streams"}


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
