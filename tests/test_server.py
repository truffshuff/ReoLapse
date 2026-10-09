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


@pytest.fixture(autouse=True)
def _job_state(tmp_path, monkeypatch):
    # keep the job queue's saved state and logs out of the real Application Support folder
    import reolapse.server.jobs as jobs_mod
    monkeypatch.setattr(jobs_mod, "STATE_DIR", tmp_path / "state")


@pytest.fixture
def env(tmp_path, monkeypatch):
    data, work = tmp_path / "share", tmp_path / "work"
    data.mkdir()  # the queue pauses itself when the share is missing
    monkeypatch.setattr(config, "DATA_ROOT", data)
    monkeypatch.setattr(config, "WORK_ROOT", work)
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    settings_mod.save({"cameras": ["Garage"]})
    return data, work


def fake_command(job: Job) -> list[str]:
    """Stand-in for the pipeline: progress, in-place status, log lines; exit code from options."""
    code = int(job.options.get("exit", 0))
    script = (
        "import json,sys;"
        "sys.stdout.write('\\rdownloading 10 MB');"
        "print('\\r@@'+json.dumps({'stage':'analyze','frac':0.5}));"
        "sys.stdout.write('\\rdownloading 20 MB');"
        "print('\\rhello from', sys.argv[1]);"
        "print('\\r@@'+json.dumps({'stage':'analyze','frac':1.0}));"
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
        log = "\n".join(done["log"])
        assert "hello from analyze" in log and "Finished in" in log
        assert "downloading" not in log  # in-place status lines don't fill the log
        text = client.get(f"/api/jobs/{job['id']}/log").text
        assert "Queued: Find moving objects" in text and "hello from analyze" in text


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
        by = cam["hourly_by"]
        assert sum(by[k][10] for k in by) == 2 and sum(by[k][17] for k in by) == 1
        r = client.get("/api/days/2026-10-01/Garage/out/synopsis_cutout_category.mp4")
        assert r.status_code == 200 and len(r.content) == 10
        assert client.get("/api/days/2026-10-01/Garage/out/..%2Fsecret").status_code == 404


def test_work_root_setting_and_share_fallback(env, tmp_path, monkeypatch):
    from datetime import date

    data, _ = env
    monkeypatch.delenv("REOLAPSE_WORK", raising=False)
    local = tmp_path / "local"
    settings_mod.save({"storage": {"work_root": str(local)}})
    monkeypatch.setattr(config, "WORK_ROOT", config._work_root())
    assert config.WORK_ROOT == local

    p = config.DayPaths("Garage", date(2026, 10, 1))
    # nothing local yet, but the share has tubes from before: read from the share, write locally
    (data / "garage" / "2026-10-01" / "tubes").mkdir(parents=True)
    assert p.tubes == data / "garage" / "2026-10-01" / "tubes"
    assert p.tubes_out == local / "garage" / "2026-10-01" / "tubes"
    # once the work area has tubes, it wins
    p.tubes_out.mkdir(parents=True)
    assert p.tubes == p.tubes_out
    assert p.published == data / "garage" / "2026-10-01" / "out"


def test_stream_settings(env):
    s = settings_mod.save({"streams": {"Porch Left": "main"}})
    assert s["streams"] == {"Porch Left": "main"}
    assert settings_mod.save({"streams": {"Garage": "sub"}})["streams"] == {"Garage": "sub"}  # replaces the map
    with pytest.raises(ValueError):
        settings_mod.save({"streams": {"Garage": "4k"}})


def test_area_query(env):
    import cv2
    import numpy as np
    from reolapse import tube_index

    data, work = env
    tubes = work / "garage" / "2026-10-01" / "tubes"
    tubes.mkdir(parents=True)
    crop = cv2.imencode(".png", np.zeros((10, 10, 4), np.uint8))[1].tobytes()
    # a person walking left->right across the top half; a car crossing the bottom
    walk = [(100 + i, 50 * i, 100, 50 * i + 40, 200, crop) for i in range(20)]
    drive = [(500 + i, 1900 - 90 * i, 400, 1900 - 90 * i + 150, 500, crop) for i in range(20)]
    for name, frames, cat in (("000100.0_person.pkl", walk, "person"), ("000500.0_vehicle.pkl", drive, "vehicle")):
        (tubes / name).write_bytes(pickle.dumps({"t0": frames[0][0], "t1": frames[-1][0], "category": cat, "frames": frames}))
    bg = work / "garage" / "2026-10-01" / "bg"
    bg.mkdir()
    cv2.imwrite(str(bg / "078.jpg"), np.zeros((576, 1920, 3), np.uint8))

    with TestClient(create_app(JobQueue(fake_command))) as client:
        top_right = client.get("/api/days/2026-10-01/Garage/objects", params={"regions": "0.4,0,1,0.5"}).json()
        assert [o["id"] for o in top_right["objects"]] == ["000100.0_person"]
        assert 115 <= top_right["objects"][0]["enter"] <= 117  # centre crosses x=0.4*1920 around frame 15
        both = client.get("/api/days/2026-10-01/Garage/objects", params={"regions": "0,0,0.2,0.5;0.4,0.6,0.6,1"}).json()
        assert {o["category"] for o in both["objects"]} == {"person", "vehicle"}
        only_cars = client.get("/api/days/2026-10-01/Garage/objects", params={"categories": "vehicle"}).json()
        assert [o["category"] for o in only_cars["objects"]] == ["vehicle"]
        assert (tubes / tube_index.INDEX_NAME).exists()  # built once and cached
        r = client.get("/api/days/2026-10-01/Garage/objects/000100.0_person/thumb")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"
        assert (tubes / "_thumbs" / "000100.0_person.png").read_bytes() == r.content  # cached for next time
        assert client.get("/api/days/2026-10-01/Garage/objects/000100.0_person/thumb").content == r.content
        small = client.get("/api/days/2026-10-01/Garage/objects/000100.0_person/thumb", params={"size": 4})
        import cv2, numpy as np
        assert small.headers["content-type"] == "image/jpeg"
        assert max(cv2.imdecode(np.frombuffer(small.content, np.uint8), cv2.IMREAD_UNCHANGED).shape[:2]) == 4
        assert client.get("/api/days/2026-10-01/Garage/objects/..%2Fx/thumb").status_code in (400, 404)


def test_queue_survives_restart(env, tmp_path):
    import asyncio

    async def scenario():
        q1 = JobQueue(fake_command)
        (a,) = q1.submit("fetch", "2026-09-30", ["Garage"])
        a.status = "running"  # pretend the server died mid-job
        everything = q1.submit("run", "2026-09-29", ["Garage"])
        assert [j.kind for j in everything] == ["fetch", "analyze", "render"]
        assert {j.group for j in everything} == {everything[0].id}
        for t in list(q1._tasks):
            t.cancel()
        q1._save()
        q2 = JobQueue(fake_command)
        q2._restore()
        assert {j.id: j.status for j in q2.jobs.values()}[a.id] == "queued"
        assert any("resuming" in line for line in q2.jobs[a.id].log)
        assert q2.submit("render", "2026-09-28", ["Garage"])[0].id == everything[-1].id + 1

    asyncio.run(scenario())


def sleepy_command(job):
    """Fake pipeline step: sleeps options['sleep'] seconds, exits with options['exit']."""
    import sys
    secs = float(job.options.get("sleep", 0.3))
    code = int(job.options.get("exit", 0)) if job.kind == job.options.get("fail_kind", job.kind) else 0
    return [sys.executable, "-c", f"import time,sys; time.sleep({secs}); sys.exit({code})"]


async def _until(cond, timeout=10):
    import asyncio
    import time as _t
    end = _t.time() + timeout
    while not cond():
        assert _t.time() < end, "timed out"
        await asyncio.sleep(0.02)


def test_lanes_run_in_parallel_but_days_keep_step_order(env):
    import asyncio

    async def scenario():
        q = JobQueue(sleepy_command)
        q.start()
        fa, aa, ra = q.submit("run", "2026-09-30", ["Garage"], {"sleep": 0.4})
        (fb,) = q.submit("fetch", "2026-09-29", ["Garage"], {"sleep": 0.6})
        await asyncio.sleep(0.05)
        assert fa.status == "running" and aa.status == "queued" and fb.status == "queued"
        assert "download" in aa.waiting and "next in the download lane" in fb.waiting
        await _until(lambda: fa.status == "done")
        await _until(lambda: aa.status == "running")
        assert fb.status == "running"  # Sep 29 downloads while Sep 30 is being analyzed
        await _until(lambda: ra.status == "done" and fb.status == "done")
        assert ra.started >= aa.finished

    asyncio.run(scenario())


def test_failure_blocks_later_steps_until_retry(env):
    import asyncio

    async def scenario():
        q = JobQueue(sleepy_command)
        q.start()
        f, a, r = q.submit("run", "2026-09-30", ["Garage"], {"sleep": 0.1, "exit": 1, "fail_kind": "fetch"})
        await _until(lambda: f.status == "failed")
        await asyncio.sleep(0.1)
        assert a.status == "queued" and "retry it" in a.waiting
        f.options = {"sleep": 0.1}
        q.retry(f.id)
        await _until(lambda: r.status == "done")
        assert a.status == "done"

    asyncio.run(scenario())


def test_move_reorders_queued_jobs(env):
    import asyncio

    async def scenario():
        q = JobQueue(sleepy_command)
        q.start()
        (busy,) = q.submit("analyze", "2026-09-01", ["Garage"], {"sleep": 0.5})  # occupies the process lane
        (x,) = q.submit("render", "2026-09-02", ["Garage"], {"sleep": 0.05})
        (y,) = q.submit("render", "2026-09-03", ["Garage"], {"sleep": 0.05})
        q.move(y.id, -1)
        assert y.order < x.order
        await _until(lambda: x.status == "done" and y.status == "done")
        assert y.started < x.started

    asyncio.run(scenario())


def test_ui_cache_headers(env, tmp_path, monkeypatch):
    import reolapse.server.app as app_mod

    ui = tmp_path / "ui"
    (ui / "assets").mkdir(parents=True)
    (ui / "index.html").write_text("<html></html>")
    (ui / "assets" / "index-abc.js").write_text("x")
    monkeypatch.setattr(app_mod, "UI_DIR", ui)
    with TestClient(create_app(JobQueue(fake_command))) as client:
        assert client.get("/").headers["cache-control"] == "no-cache"
        assert "immutable" in client.get("/assets/index-abc.js").headers["cache-control"]


def test_pause_stops_running_job_and_resume_continues(env):
    import asyncio

    async def scenario():
        q = JobQueue(sleepy_command)
        q.start(watch_share=False)
        (a,) = q.submit("analyze", "2026-09-30", ["Garage"], {"sleep": 1.0})
        await _until(lambda: a.status == "running" and a.proc is not None)
        q.pause("you paused it")
        await _until(lambda: a.status == "queued")
        assert "queue paused" in a.waiting and any("Paused" in l for l in a.log)
        await asyncio.sleep(0.2)
        assert a.status == "queued"  # nothing starts while paused
        q.resume()
        await _until(lambda: a.status == "done")

    asyncio.run(scenario())


def test_auto_pause_when_share_disappears(env):
    import asyncio

    async def scenario():
        here = {"ok": True}
        q = JobQueue(sleepy_command)
        q.share_check = lambda: here["ok"]
        q.start(watch_share=False)
        q._spawn(q._watch_share(every=0.05))
        (a,) = q.submit("fetch", "2026-09-30", ["Garage"], {"sleep": 1.0})
        await _until(lambda: a.status == "running")
        here["ok"] = False  # laptop leaves home
        await _until(lambda: q.paused and q.auto_paused and a.status == "queued")
        here["ok"] = True  # back on the home network
        await _until(lambda: not q.paused)
        await _until(lambda: a.status == "done")

    asyncio.run(scenario())


def test_nvr_login_saved_to_settings_and_keychain(env, monkeypatch):
    import keyring

    store = {}
    monkeypatch.setattr(keyring, "set_password", lambda svc, acct, pw: store.__setitem__((svc, acct), pw))
    monkeypatch.setattr(keyring, "get_password", lambda svc, acct: store.get((svc, acct)))
    for k in ("REOLINK_HOST", "REOLINK_USER", "REOLINK_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    with TestClient(create_app(JobQueue(fake_command))) as client:
        assert client.get("/api/nvr").json()["password_set"] is False
        r = client.put("/api/nvr", json={"host": "10.0.0.5", "user": "viewer", "password": "s3cret!"})
        assert r.status_code == 200 and r.json() == {"host": "10.0.0.5", "user": "viewer", "port": 80,
                                                      "password_set": True, "source": "app"}
        assert store == {("Reolapse NVR", "viewer@10.0.0.5"): "s3cret!"}
        assert "s3cret" not in (settings_mod.SETTINGS_PATH.read_text())  # never in the settings file
        assert client.put("/api/nvr", json={"host": "h", "user": "u", "password": "a&b"}).status_code == 400
        # keeping the stored password when none is sent
        assert client.put("/api/nvr", json={"host": "10.0.0.5", "user": "viewer", "port": 81}).json()["password_set"]
    monkeypatch.setenv("REOLINK_HOST", "envhost")
    monkeypatch.setenv("REOLINK_USER", "envuser")
    monkeypatch.setenv("REOLINK_PASSWORD", "envpw")
    from reolapse import nvr as nvrmod
    assert nvrmod.credentials()["source"] == ".env"


def test_move_is_published_while_paused(env):
    import asyncio

    async def scenario():
        q = JobQueue(sleepy_command)
        q.start()
        q.pause("you paused it")
        (x,) = q.submit("render", "2026-09-02", ["Garage"])
        (y,) = q.submit("render", "2026-09-03", ["Garage"])
        events = q.subscribe()
        q.move(y.id, -1)
        seen = {}
        while not events.empty():
            e = events.get_nowait()
            if "job" in e:
                seen[e["job"]["id"]] = e["job"]["order"]
        assert seen[y.id] < seen[x.id]

    asyncio.run(scenario())


def test_remove_finished_jobs(env):
    with TestClient(create_app(JobQueue(fake_command))) as client:
        a = client.post("/api/jobs", json={"kind": "render", "day": "2026-09-02", "cameras": ["Garage"]}).json()
        b = client.post("/api/jobs", json={"kind": "render", "day": "2026-09-03", "cameras": ["Garage"]}).json()
        for _ in range(100):
            if all(j["status"] == "done" for j in client.get("/api/jobs").json()):
                break
            import time; time.sleep(0.05)
        assert client.delete(f"/api/jobs/{a['id']}").json() == {"removed": [a["id"]]}
        assert [j["id"] for j in client.get("/api/jobs").json()] == [b["id"]]
        assert client.delete("/api/jobs").json() == {"removed": [b["id"]]}
        assert client.get("/api/jobs").json() == []
        assert client.delete("/api/jobs/99").status_code == 404


def test_saving_settings_keeps_a_login_saved_since(env, monkeypatch):
    import keyring
    store = {}
    monkeypatch.setattr(keyring, "set_password", lambda s, a, p: store.__setitem__((s, a), p))
    monkeypatch.setattr(keyring, "get_password", lambda s, a: store.get((s, a)))
    with TestClient(create_app(JobQueue(fake_command))) as client:
        page = client.get("/api/settings").json()  # the Settings page opens: no login yet
        assert client.put("/api/nvr", json={"host": "10.0.0.2", "user": "reolapse", "port": 80,
                                            "password": "secret"}).status_code == 200
        page["render"]["drift"] = 3
        out = client.put("/api/settings", json=page).json()  # Save changes with the page's old copy
        assert out["nvr"]["host"] == "10.0.0.2" and out["nvr"]["user"] == "reolapse"
        assert out["render"]["drift"] == 3
        assert client.get("/api/nvr").json()["password_set"] is True


def test_macos_malloc_noise_is_dropped(env):
    q = JobQueue(fake_command)
    job = Job("analyze", "2026-09-30", ["Garage"])
    q._segment(job, "scanned up to 6:59 PM · 789 objects so far   python(7332) MallocStackLogging: "
                    "can't turn off malloc stack logging because it was not enabled.", "\n")
    assert job.status_line == "scanned up to 6:59 PM · 789 objects so far" and not job.log
    q._segment(job, "python(7332) MallocStackLogging: can't turn off malloc stack logging", "\n")
    assert not job.log
    q._segment(job, "   footage: 32 recording files", "\n")
    assert job.log[-1].endswith("footage: 32 recording files")


def test_analysis_settings_are_fixed_when_queued(env):
    from reolapse import settings as settings_mod

    settings_mod.save({"analysis": {"model": "yolo26l-seg.pt", "imgsz": 1920, "fps": 10.0}})
    q = JobQueue(fake_command)
    q.paused = True  # nothing starts: only the queued arguments matter here
    fetch, analyze, render = q.submit("run", "2026-09-29", ["Garage"])
    test = q.submit("analyze", "2026-10-01", ["Porch Left"], {"imgsz": 1280})[0]
    settings_mod.save({"analysis": {"imgsz": 960, "model": "yolo26n-seg.pt"}})  # changed later
    cfg = settings_mod.load()
    args = pipeline_args(analyze, cfg)
    assert args[args.index("--imgsz") + 1] == "1920" and args[args.index("--model") + 1] == "yolo26l-seg.pt"
    args = pipeline_args(test, cfg)
    assert args[args.index("--imgsz") + 1] == "1280" and args[args.index("--model") + 1] == "yolo26l-seg.pt"
    assert "imgsz" not in fetch.options and "imgsz" not in render.options


def test_jobs_from_older_versions_get_their_settings_fixed_on_restart(env):
    from reolapse import settings as settings_mod

    settings_mod.save({"analysis": {"model": "yolo26l-seg.pt", "imgsz": 1920}})
    q = JobQueue(fake_command)
    q.paused = True
    job = q.submit("analyze", "2026-09-29", ["Garage"])[0]
    for k in ("fps", "types", "model", "imgsz"):
        del job.options[k]  # as saved by an older version
    job.status = "running"
    q._save()
    q2 = JobQueue(fake_command)
    q2._restore()
    assert q2.jobs[job.id].options["imgsz"] == 1920 and q2.jobs[job.id].status == "queued"


def test_cancelled_download_does_not_block_and_paused_queue_says_why(env):
    import asyncio

    async def scenario():
        q = JobQueue(sleepy_command)
        q.start()
        q.pause("you paused it")
        (old,) = q.submit("fetch", "2026-10-01", ["Porch Left"])
        old.status = "done"  # footage already downloaded earlier
        fetch, analyze, render = q.submit("run", "2026-10-01", ["Porch Left"])
        (failed,) = q.submit("fetch", "2026-10-02", ["Garage"])
        (later,) = q.submit("analyze", "2026-10-02", ["Garage"])
        failed.status = "failed"
        q.cancel(fetch.id)  # not needed: the footage is on the share
        q._schedule()
        assert analyze.waiting == "queue paused: you paused it"
        assert later.waiting.startswith("queue paused: you paused it; then this day's download failed")
        q.resume()
        await _until(lambda: analyze.status == "done")

    asyncio.run(scenario())


def test_report_areas_in_settings(env):
    import pytest
    from reolapse import settings as settings_mod

    area = {"name": "Driveway", "boxes": [{"x0": 0.4, "y0": 0.4, "x1": 0.6, "y1": 1.0}]}
    out = settings_mod.save({"areas": {"Garage": [area]}})
    assert out["areas"]["Garage"][0]["name"] == "Driveway"
    assert settings_mod.save({"areas": {}})["areas"] == {}  # the whole map is replaced, so areas can be deleted
    with pytest.raises(ValueError):
        settings_mod.save({"areas": {"Garage": [{"name": "x", "boxes": [{"x0": 0, "y0": 0, "x1": 2, "y1": 1}]}]}})
    with pytest.raises(ValueError):
        settings_mod.save({"areas": {"Garage": [{"name": "", "boxes": [area["boxes"][0]]}]}})
