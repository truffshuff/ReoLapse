import pickle
from datetime import date

import cv2
import numpy as np

from reolapse import config, render
from reolapse.server.jobs import Job, pipeline_args
from reolapse import settings as settings_mod


def _day(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "share")
    monkeypatch.setattr(config, "WORK_ROOT", tmp_path / "work")
    paths = config.DayPaths("Garage", date(2026, 10, 1))
    paths.backgrounds_out.mkdir(parents=True)
    for slot in (60, 61):  # 10:00 and 10:10
        cv2.imwrite(str(paths.backgrounds_out / f"{slot:03d}.jpg"), np.full((96, 320, 3), 90, np.uint8))
    paths.tubes_out.mkdir(parents=True)
    crop = cv2.imencode(".png", np.full((20, 10, 4), 255, np.uint8))[1].tobytes()
    for i, cat in enumerate(["person", "vehicle", "person"]):
        t0 = 36000 + 60 * i
        frames = [(t0 + k * 0.2, 10 + 5 * k, 30, 20 + 5 * k, 50, crop) for k in range(20)]
        tube = {"t0": frames[0][0], "t1": frames[-1][0], "category": cat, "frames": frames}
        (paths.tubes_out / f"{t0:08.1f}_{cat}.pkl").write_bytes(pickle.dumps(tube))
    return paths


def _render(**kw):
    args = dict(styles=["cutout"], color="category", length=4.0, density=6.0, speed=2.0, max_tube=20.0,
                fps=10.0, labels=True, only=None, stills=True, video=True, drift_min=10.0)
    args.update(kw)
    render.run(["Garage"], date(2026, 10, 1), **args)


def test_per_type_extra_videos_and_filtered_render_keeps_stills(tmp_path, monkeypatch):
    paths = _day(tmp_path, monkeypatch)
    _render(per_type=["person", "animal"])  # no animals that day: no animal video
    out = sorted(p.name for p in paths.published.iterdir())
    assert "synopsis_cutout_category.mp4" in out and "synopsis_cutout_category_person.mp4" in out
    assert not any("animal" in n for n in out)
    assert {"trails.jpg", "strobe.jpg", "heatmap.jpg"} <= set(out)
    trails_before = (paths.published / "trails.jpg").read_bytes()

    _render(only={"vehicle"})
    out = sorted(p.name for p in paths.published.iterdir())
    assert "synopsis_cutout_category_vehicle.mp4" in out
    assert (paths.published / "trails.jpg").read_bytes() == trails_before  # whole-day stills untouched


def test_job_args_for_type_videos(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    cfg = settings_mod.save({"render": {"per_type": ["person"]}})
    args = pipeline_args(Job("render", "2026-10-01", ["Garage"]), cfg)
    assert args[args.index("--per-type") + 1] == "person" and "--only" not in args
    args = pipeline_args(Job("render", "2026-10-01", ["Garage"], {"only": ["vehicle", "bogus"]}), cfg)
    assert args[args.index("--only") + 1] == "vehicle" and "--per-type" not in args
