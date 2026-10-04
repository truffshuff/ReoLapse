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
    assert {f"{s}_{k}.jpg" for s in ("trails", "strobe", "heatmap") for k in ("person", "vehicle")} <= set(out)
    trails_before = (paths.published / "trails.jpg").read_bytes()

    _render(only={"vehicle"})
    out = sorted(p.name for p in paths.published.iterdir())
    assert "synopsis_cutout_category_vehicle.mp4" in out
    assert (paths.published / "trails.jpg").read_bytes() == trails_before  # whole-day stills untouched


def test_pictures_only_render(tmp_path, monkeypatch):
    paths = _day(tmp_path, monkeypatch)
    _render(video=False)
    out = {p.name for p in paths.published.iterdir()}
    assert not any(n.endswith(".mp4") for n in out)
    assert {"trails.jpg", "trails_person.jpg", "heatmap_vehicle.jpg", "strobe_person.jpg"} <= out
    assert not any("animal" in n for n in out)
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    args = pipeline_args(Job("render", "2026-10-01", ["Garage"], {"no_video": True}), settings_mod.load())
    assert "--no-video" in args


def test_job_args_for_type_videos(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    cfg = settings_mod.save({"render": {"per_type": ["person"]}})
    args = pipeline_args(Job("render", "2026-10-01", ["Garage"]), cfg)
    assert args[args.index("--per-type") + 1] == "person" and "--only" not in args
    args = pipeline_args(Job("render", "2026-10-01", ["Garage"], {"only": ["vehicle", "bogus"]}), cfg)
    assert args[args.index("--only") + 1] == "vehicle" and "--per-type" not in args


def test_travel_filter_and_prune(tmp_path):
    from reolapse import tube_index

    car = [(100, 50, 200, 100)] * 10
    wobble = [(100 + (i % 3) * 4, 50 + (i % 2) * 3, 200 + (i % 3) * 4, 100) for i in range(20)]  # parked, jittery
    drive = [(100 + 30 * i, 50, 200 + 30 * i, 100) for i in range(10)]
    garden = [(300 + 6 * (i % 5), 40, 340 + 6 * (i % 5), 140) for i in range(20)]  # person moving about in place
    assert not tube_index.really_moved("vehicle", car)
    assert not tube_index.really_moved("vehicle", wobble)
    assert tube_index.really_moved("vehicle", drive)
    assert tube_index.really_moved("person", garden)

    d = tmp_path / "tubes"
    d.mkdir()
    metas = []
    for name, cat, boxes in (("1.0_vehicle", "vehicle", wobble), ("2.0_vehicle", "vehicle", drive)):
        (d / f"{name}.pkl").write_bytes(b"x")
        metas.append(tube_index.tube_meta(f"{name}.pkl", 0.0, 4.0, cat, [(i * 0.5, *b) for i, b in enumerate(boxes)]))
    kept, gone = tube_index.prune(d, metas, (1920, 576))
    assert [m["id"] for m in kept] == ["2.0_vehicle"] and gone == 1
    assert (d / "_pruned" / "1.0_vehicle.pkl").exists() and not (d / "1.0_vehicle.pkl").exists()
    assert [t["id"] for t in tube_index.load(d)["tubes"]] == ["2.0_vehicle"]


def test_settings_are_recorded_for_the_app(tmp_path, monkeypatch):
    import json
    from reolapse.analyze import write_analysis_info
    from reolapse.config import stream_of
    from reolapse.media import Recording

    assert stream_of(["main_1.mp4", "main_2.mp4"]) == "main" and stream_of(["sub_1.mp4", "main_2.mp4"]) == "mixed"
    assert stream_of([]) is None
    paths = _day(tmp_path, monkeypatch)
    rec = Recording(paths.raw / "main_20261001_000000.mp4", 0, 3600, 5120, 1552)
    write_analysis_info(paths, [rec], fps=10.0, model="yolo26l-seg.pt", imgsz=1920, types={"all"}, hours=(0, 24))
    info = json.loads((paths.tubes_out / "analysis.json").read_text())
    assert info["stream"] == "main" and info["size"] == [2560, 776] and info["model"] == "yolo26l-seg.pt"
    _render(stills=False)
    side = json.loads((paths.published / "synopsis_cutout_category.json").read_text())
    assert side["style"] == "cutout" and side["analysis"]["imgsz"] == 1920 and side["categories"] == ["person", "vehicle"]


def test_resumed_job_keeps_cameras_it_finished(tmp_path, monkeypatch):
    import time as _time
    from reolapse.analyze import finished_since, write_analysis_info
    from reolapse.media import Recording

    paths = _day(tmp_path, monkeypatch)
    settings = dict(fps=10.0, model="yolo26l-seg.pt", imgsz=1920, types={"all"}, hours=(0, 24))
    queued = _time.time() - 60
    assert not finished_since(paths, queued, **settings)  # nothing recorded yet
    write_analysis_info(paths, [Recording(paths.raw / "main_1.mp4", 0, 3600, 5120, 1552)], **settings)
    assert finished_since(paths, queued, **settings)  # finished by this job: skip on resume
    assert not finished_since(paths, _time.time() + 60, **settings)  # a newer job re-analyzes
    assert not finished_since(paths, queued, **{**settings, "model": "yolo26x-seg.pt"})  # other settings
    assert not finished_since(paths, None, **settings)  # the CLI without --done-since
    args = pipeline_args(Job("analyze", "2026-10-01", ["Garage"], created=queued), settings_mod.load())
    assert args[args.index("--done-since") + 1] == f"{queued:.0f}"


def test_resumed_job_keeps_cameras_finished_by_older_versions(tmp_path, monkeypatch):
    import time as _time
    from reolapse.analyze import CHECKPOINT, finished_since

    paths = _day(tmp_path, monkeypatch)
    settings = dict(fps=10.0, model="yolo26l-seg.pt", imgsz=1920, types={"all"}, hours=(0, 24))
    queued = _time.time() - 60
    (paths.tubes_out / "index.json").write_text('{"width":2560,"height":776,"tubes":[]}')
    assert finished_since(paths, queued, **settings)
    (paths.tubes_out / CHECKPOINT).write_text("{}")  # still in progress
    assert not finished_since(paths, queued, **settings)
