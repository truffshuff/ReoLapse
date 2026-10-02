"""Paths and per-run settings."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Share: raw recordings, the NVR event list, and the finished videos/stills.
DATA_ROOT = Path(os.environ.get("REOLAPSE_DATA", "/Volumes/Photos/ReoLink"))
# Local working area for the random-access intermediates (backgrounds, object tubes, rendering).
# Unset = keep everything on the share. Avoid ~/Documents: it is iCloud-synced.
WORK_ROOT = Path(os.environ["REOLAPSE_WORK"]).expanduser() if os.environ.get("REOLAPSE_WORK") else DATA_ROOT
MODEL_DIR = Path(__file__).resolve().parents[2] / "models"

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

    @property
    def backgrounds(self) -> Path:
        return self.work / "bg"

    @property
    def tubes(self) -> Path:
        return self.work / "tubes"

    @property
    def out(self) -> Path:
        """Where render writes (local when REOLAPSE_WORK is set)."""
        return self.work / "out"

    @property
    def published(self) -> Path:
        """Final home of the outputs on the share."""
        return self.root / "out"
