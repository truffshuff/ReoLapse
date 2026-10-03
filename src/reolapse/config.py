"""Paths and per-run settings."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()



def _setting(*keys: str) -> str:
    """A value from the app's settings file ('' if unset or unreadable)."""
    from .settings import load  # settings.py imports nothing from here

    try:
        value = load()
        for k in keys:
            value = value[k]
        return value or ""
    except Exception:
        return ""


def _data_root() -> Path:
    """Share: raw recordings, the NVR event list, and the finished videos/stills.
    REOLAPSE_DATA wins, then the storage.data_root setting."""
    return Path(os.environ.get("REOLAPSE_DATA") or _setting("storage", "data_root") or "/Volumes/Photos/ReoLink").expanduser()


DATA_ROOT = _data_root()


def _work_root() -> Path:
    """Local working area for the random-access intermediates (backgrounds, object tubes, rendering).

    REOLAPSE_WORK wins, then the app's storage.work_root setting; unset = everything on the share.
    Avoid ~/Documents: it is iCloud-synced.
    """
    if os.environ.get("REOLAPSE_WORK"):
        return Path(os.environ["REOLAPSE_WORK"]).expanduser()
    configured = _setting("storage", "work_root")
    return Path(configured).expanduser() if configured else DATA_ROOT


def _model_dir() -> Path:
    """YOLO weights: REOLAPSE_MODELS (set by the desktop app), else the source checkout's models/,
    else next to the app's settings. Missing weights download there on first use."""
    if os.environ.get("REOLAPSE_MODELS"):
        return Path(os.environ["REOLAPSE_MODELS"]).expanduser()
    checkout = Path(__file__).resolve().parents[2] / "models"
    if checkout.is_dir():
        return checkout
    from .settings import SETTINGS_PATH

    return SETTINGS_PATH.parent / "models"


WORK_ROOT = _work_root()
MODEL_DIR = _model_dir()

DEFAULT_CAMERAS = ["Garage", "Porch Left", "Back Yard Right"]

# YOLO (COCO) class name -> reolapse category
CATEGORIES = {
    "person": "person",
    "bicycle": "vehicle", "car": "vehicle", "motorcycle": "vehicle", "bus": "vehicle", "truck": "vehicle",
    "bird": "animal", "cat": "animal", "dog": "animal", "horse": "animal", "sheep": "animal",
    "cow": "animal", "bear": "animal",
}


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


@dataclass(frozen=True)
class DayPaths:
    camera: str
    day: date

    @property
    def root(self) -> Path:
        return DATA_ROOT / slug(self.camera) / self.day.isoformat()

    @property
    def work(self) -> Path:
        return WORK_ROOT / slug(self.camera) / self.day.isoformat()

    @property
    def raw(self) -> Path:
        return self.root / "raw"

    @property
    def files_index(self) -> Path:
        return self.root / "files.json"

    @property
    def events(self) -> Path:
        return self.root / "events.json"

    def _existing(self, sub: str) -> Path:
        """Read location: the work area, else (for days processed before it was set) the share."""
        local = self.work / sub
        if local.exists() or self.work == self.root:
            return local
        shared = self.root / sub
        return shared if shared.exists() else local

    @property
    def backgrounds(self) -> Path:
        return self._existing("bg")

    @property
    def tubes(self) -> Path:
        return self._existing("tubes")

    @property
    def backgrounds_out(self) -> Path:
        """Where analysis writes backgrounds (always the work area)."""
        return self.work / "bg"

    @property
    def tubes_out(self) -> Path:
        """Where analysis writes tubes (always the work area)."""
        return self.work / "tubes"

    @property
    def out(self) -> Path:
        """Where render writes (local when REOLAPSE_WORK is set)."""
        return self.work / "out"

    @property
    def published(self) -> Path:
        """Final home of the outputs on the share."""
        return self.root / "out"
