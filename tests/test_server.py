import json
import pickle
import sys
import time

import pytest
from fastapi.testclient import TestClient

from reolapse import config
from reolapse import settings as settings_mod
from reolapse.server.app import create_app
from reolapse.server.jobs import Job, JobQueue, pipeline_args


@pytest.fixture
def env(tmp_path, monkeypatch):
    data, work = tmp_path / "share", tmp_path / "work"
    monkeypatch.setattr(config, "DATA_ROOT", data)
    monkeypatch.setattr(config, "WORK_ROOT", work)
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    settings_mod.save({"cameras": ["Garage"]})
    return data, work


def fake_command(job: Job) -> list[str]:
    """Stand-in for the pipeline: two progress lines, one log line, exit code from options."""
    code = int(job.options.get("exit", 0))
    script = (
        "import json,sys;"
        "print('@@'+json.dumps({'stage':'analyze','frac':0.5}));"
        "print('hello from', sys.argv[1]);"
        "print('@@'+json.dumps({'stage':'analyze','frac':1.0}));"
        f"sys.exit({code})"
    )
    return [sys.executable, "-c", script, job.kind]


def wait_for(client, job_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] not in ("queued", "running"):
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_settings_merge_and_reject(env, tmp_path):
    s = settings_mod.save({"render": {"drift": 5}})
    assert s["render"]["drift"] == 5 and s["render"]["color"] == "category"
    with pytest.raises(ValueError):
        settings_mod.save({"render": {"nope": 1}})


def test_pipeline_args_use_settings_and_overrides(env):
    cfg = settings_mod.load()
    args = pipeline_args(Job("run", "2026-10-01", ["Garage"], {"drift": 0, "skip_bg": True}), cfg)
    assert args[:5] == ["run", "--day", "2026-10-01", "--camera", "Garage"]
    assert args[args.index("--drift") + 1] == "0"
    assert "--skip-bg" in args and args[args.index("--fps") + 1] == "5.0"


def test_job_runs_and_reports_progress(env):
    with TestClient(create_app(JobQueue(fake_command))) as client:
        job = client.post("/api/jobs", json={"kind": "analyze", "day": "2026-10-01"}).json()
        done = wait_for(client, job["id"])
        assert done["status"] == "done"
        assert done["progress"] == {"stage": "analyze", "frac": 1.0}
        assert "hello from analyze" in done["log"]


def test_failed_job_and_validation(env):
    with TestClient(create_app(JobQueue(fake_command))) as client:
        job = client.post("/api/jobs", json={"kind": "render", "day": "2026-10-01", "options": {"exit": 3}}).json()
        assert wait_for(client, job["id"])["status"] == "failed"
        assert client.post("/api/jobs", json={"kind": "bogus", "day": "2026-10-01"}).status_code == 400
        assert client.post("/api/jobs", json={"kind": "fetch", "day": "yesterday"}).status_code == 400
        assert client.post("/api/jobs", json={"kind": "fetch", "day": "2026-10-01",
                                               "cameras": ["Nope"]}).status_code == 404


def test_library_status(env):
    data, work = env
    day = data / "garage" / "2026-10-01"
    (day / "raw").mkdir(parents=True)
    (day / "files.json").write_text(json.dumps([{"path": "a.mp4", "start": "x", "end": "y", "stream": "sub"}]))
    tubes = work / "garage" / "2026-10-01" / "tubes"
    tubes.mkdir(parents=True)
    for name in ("036000.0_person.pkl", "036100.0_person.pkl", "061200.5_vehicle.pkl"):
        (tubes / name).write_bytes(pickle.dumps({}))
    out = day / "out"
    out.mkdir()
    (out / "synopsis_cutout_category.mp4").write_bytes(b"\0" * 10)

    with TestClient(create_app(JobQueue(fake_command))) as client:
        days = client.get("/api/days").json()
        assert [d["day"] for d in days] == ["2026-10-01"]
        cam = days[0]["cameras"][0]
        assert cam["status"] == "rendered"
        assert cam["counts"] == {"person": 2, "vehicle": 1, "animal": 0}
        assert cam["hourly"][10] == 2 and cam["hourly"][17] == 1
        r = client.get("/api/days/2026-10-01/Garage/out/synopsis_cutout_category.mp4")
        assert r.status_code == 200 and len(r.content) == 10
        assert client.get("/api/days/2026-10-01/Garage/out/..%2Fsecret").status_code == 404
