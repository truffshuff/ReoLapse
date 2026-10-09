"""HTTP API for the desktop app: library, jobs (with a live event stream), settings, media."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import config, review
from .. import settings as settings_mod
from ..config import DayPaths
from pathlib import Path

# Segmentation models offered in Settings. Speeds measured on an M2 at detector size 1280; any other
# *.pt found in models/ is listed too. Missing ones download automatically on first use.
MODEL_CATALOG = [
    {"file": "yolo26n-seg.pt", "name": "YOLO26 nano", "speed": "fastest (~40 frames/s)",
     "about": "Recommended. Plenty for people, cars and pets at normal distances."},
    {"file": "yolo26s-seg.pt", "name": "YOLO26 small", "speed": "about 2x slower (~22 frames/s)",
     "about": "Finds more small or distant objects (far side of the street) and draws tighter outlines."},
    {"file": "yolo26m-seg.pt", "name": "YOLO26 medium", "speed": "about 4x slower",
     "about": "Finds more small objects; not reliably better than nano at telling people from animals."},
    {"file": "yolo26l-seg.pt", "name": "YOLO26 large", "speed": "about 5x slower",
     "about": "Clearly better on hard cases: someone bent over, cut off by the edge of the picture or "
              "washed out by night lighting is called a person, not a dog or a bird. Little slower than medium."},
    {"file": "yolo26x-seg.pt", "name": "YOLO26 extra large", "speed": "about 10x slower",
     "about": "The most accurate; a whole-day scan takes many hours, more so on Clear streams. "
              "135 MB download on first use."},
    {"file": "yolo11n-seg.pt", "name": "YOLO11 nano", "speed": "fastest",
     "about": "Previous generation nano model; kept for comparison."},
]

# The desktop UI's production build (app.nosync/dist), hosted at / so the app and API share an origin.
UI_DIR = Path(os.environ.get("REOLAPSE_UI_DIR", Path(__file__).resolve().parents[3] / "app.nosync" / "dist"))
from . import library
from .jobs import JobQueue


class NvrLogin(BaseModel):
    host: str
    user: str
    port: int = 80
    password: str | None = None  # omitted/empty = keep the stored one


class KeepBody(BaseModel):
    note: str | None = None


class CorrectionBody(BaseModel):
    value: str | None = None  # person / vehicle / animal / hidden; None undoes the correction


class TokenBody(BaseModel):
    token: str | None = None  # empty = remove


class JobRequest(BaseModel):
    kind: str
    day: str
    cameras: list[str] | None = None
    options: dict = {}


def _check_day(day: str) -> str:
    if not library.DAY_RE.fullmatch(day):
        raise HTTPException(400, "day must be YYYY-MM-DD")
    return day


def _check_camera(camera: str) -> str:
    if camera not in settings_mod.load()["cameras"]:
        raise HTTPException(404, f"camera {camera!r} is not configured")
    return camera


_recordings_cache: dict[tuple[str, str], tuple[float, list]] = {}


def _recordings(paths: DayPaths) -> list:
    """Recording list for a camera-day, cached until its files.json changes (probing is slow on the share)."""
    from ..media import load_recordings

    key = (paths.camera, paths.day.isoformat())
    mtime = paths.files_index.stat().st_mtime if paths.files_index.exists() else 0.0
    hit = _recordings_cache.get(key)
    if hit is None or hit[0] != mtime:
        hit = (mtime, load_recordings(paths, probe=False))
        _recordings_cache[key] = hit
    return hit[1]


class UIFiles(StaticFiles):
    """The built UI. index.html must never be cached (it names the current build's files); the
    content-hashed files under assets/ can be cached forever. Without this, Electron kept showing
    an old build after an update."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if path.startswith("assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response


_index_cache: dict[tuple[str, str], tuple[float, dict]] = {}


def _tube_index(paths: DayPaths) -> dict | None:
    """The day's tube index (cached; built once from the tube files for older days)."""
    import cv2

    from .. import tube_index

    f = paths.tubes / tube_index.INDEX_NAME
    key = (paths.camera, paths.day.isoformat())
    mtime = f.stat().st_mtime if f.exists() else 0.0
    hit = _index_cache.get(key)
    if hit and hit[0] == mtime and mtime:
        return hit[1]
    size = None
    bg = next(iter(sorted(paths.backgrounds.glob("*.jpg"))), None) if paths.backgrounds.is_dir() else None
    if bg is not None and (img := cv2.imread(str(bg))) is not None:
        size = (img.shape[1], img.shape[0])
    index = tube_index.load(paths.tubes, size)
    if index is not None:
        _index_cache[key] = (f.stat().st_mtime if f.exists() else 0.0, index)
    return index


def create_app(queue: JobQueue | None = None, updater=None) -> FastAPI:
    from .updates import Updater

    queue = queue or JobQueue()
    updater = updater or Updater(lambda: settings_mod.load()["updates"]["repo"])

    from .scheduler import Scheduler

    scheduler = Scheduler(queue, queue.state_dir)
    storage_cache: dict = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        queue.start()
        queue._spawn(scheduler.loop())
        yield
        await queue.stop()

    app = FastAPI(title="reolapse", lifespan=lifespan)
    app.state.queue = queue

    # ---------------------------------------------------------------- status
    @app.get("/api/health")
    def health():
        return {"ok": True, "data_root": str(config.DATA_ROOT), "work_root": str(config.WORK_ROOT),
                "data_root_available": config.DATA_ROOT.is_dir()}

    # ---------------------------------------------------------------- library
    @app.get("/api/days")
    def list_days():
        return library.days(settings_mod.load()["cameras"])

    @app.get("/api/days/{day}/{camera}")
    def camera_day(day: str, camera: str, fresh: bool = False):
        return library.camera_day(_check_camera(camera), _check_day(day), fresh=fresh)

    def _scaled(f: Path, w: int):
        """A JPEG picture at most `w` pixels wide (reports: print size, not 2560 px), cached in the
        working folder so it isn't rescaled for every report."""
        import cv2

        w = max(200, min(w, 4000))
        st = f.stat()
        import hashlib

        key = hashlib.sha1(f"{f}|{st.st_mtime}|{st.st_size}".encode()).hexdigest()[:16]
        cache = config.WORK_ROOT / "_scaled" / f"{key}_{w}.jpg"
        if not cache.exists():
            img = cv2.imread(str(f))
            if img is None or img.shape[1] <= w:
                return FileResponse(f, media_type="image/jpeg")
            img = cv2.resize(img, (w, round(img.shape[0] * w / img.shape[1])), interpolation=cv2.INTER_AREA)
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_suffix(".tmp")
            tmp.write_bytes(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes())
            tmp.replace(cache)
        return FileResponse(cache, media_type="image/jpeg")

    @app.get("/api/days/{day}/{camera}/out/{name}")
    def output(day: str, camera: str, name: str, w: int = 0):
        f = library.output_file(_check_camera(camera), _check_day(day), name)
        if f is None:
            raise HTTPException(404, "no such output")
        if w and f.suffix.lower() == ".jpg":
            return _scaled(f, w)
        return FileResponse(f)  # supports Range requests, so <video> can seek

    @app.get("/api/days/{day}/{camera}/bg/{slot}")
    def background(day: str, camera: str, slot: int, w: int = 0):
        f = library.background_file(_check_camera(camera), _check_day(day), slot)
        if f is None:
            raise HTTPException(404, "no such background")
        return _scaled(f, w) if w else FileResponse(f, media_type="image/jpeg")

    @app.get("/api/cameras/{camera}/scene")
    def camera_scene(camera: str, w: int = 1280):
        """The camera's latest midday picture."""
        f = library.scene_file(_check_camera(camera))
        if f is None:
            raise HTTPException(404, "no picture of this camera yet")
        return _scaled(f, w)

    @app.get("/api/days/{day}/{camera}/clip")
    async def clip(day: str, camera: str, t: float, dur: float = 30.0):
        """Original footage starting at day-second `t`, streamed as fragmented MP4 (no re-encode)."""
        paths = DayPaths(_check_camera(camera), date.fromisoformat(_check_day(day)))
        dur = max(1.0, min(dur, 600.0))
        try:
            recs = await asyncio.to_thread(_recordings, paths)
        except SystemExit:
            raise HTTPException(404, "no recordings for that day")
        rec = next((r for r in recs if r.start <= t < r.end), None)
        if rec is None:
            raise HTTPException(404, "no recording covers that time")
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-v", "error", "-ss", f"{t - rec.start:.3f}", "-i", str(rec.path), "-t", f"{dur:.3f}",
            "-map", "0:v", "-map", "0:a?", "-c", "copy", "-movflags", "frag_keyframe+empty_moov+default_base_moof",
            "-f", "mp4", "-", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)

        async def body():
            try:
                while chunk := await proc.stdout.read(1 << 16):
                    yield chunk
            finally:
                if proc.returncode is None:
                    proc.kill()
                await proc.wait()

        return StreamingResponse(body(), media_type="video/mp4")

    @app.get("/api/days/{day}/{camera}/objects")
    async def objects(day: str, camera: str, regions: str = "", categories: str = ""):
        """Objects whose box centre entered any region. regions: "x0,y0,x1,y1;..." normalised 0..1
        (no regions = every object). categories: comma list."""
        from .. import tube_index

        paths = DayPaths(_check_camera(camera), date.fromisoformat(_check_day(day)))
        index = await asyncio.to_thread(_tube_index, paths)
        if index is None:
            raise HTTPException(404, "this day hasn't been analyzed")
        try:
            boxes = [tuple(float(v) for v in r.split(",")) for r in regions.split(";") if r.strip()]
        except ValueError:
            raise HTTPException(400, "regions must be x0,y0,x1,y1;...")
        if any(len(b) != 4 for b in boxes):
            raise HTTPException(400, "regions must be x0,y0,x1,y1;...")
        boxes = [(min(a, c), min(b, d), max(a, c), max(b, d)) for a, b, c, d in boxes] or [(0, 0, 1, 1)]
        cats = {c for c in categories.split(",") if c} or None
        index = review.apply(index, review.load_corrections(paths.tubes), review.zones_for(paths.camera))
        return {"width": index["width"], "height": index["height"], "objects": tube_index.query(index, boxes, cats)}

    @app.put("/api/days/{day}/{camera}/objects/{obj_id}/correction")
    def correct_object(day: str, camera: str, obj_id: str, body: CorrectionBody):
        """Fix an object's type ("person" / "vehicle" / "animal"), hide it ("hidden"), or undo (null)."""
        if not re.fullmatch(r"[0-9.]+_[a-z]+(_\d+)?", obj_id):
            raise HTTPException(400, "bad object id")
        paths = DayPaths(_check_camera(camera), date.fromisoformat(_check_day(day)))
        if not (paths.tubes / f"{obj_id}.pkl").is_file():
            raise HTTPException(404, "no such object")
        try:
            for tubes in paths.copies("tubes") or [paths.tubes]:  # this Mac's copy and the share's
                review.save_correction(tubes, obj_id, body.value)
        except (ValueError, OSError) as e:
            raise HTTPException(400, str(e))
        library._cache.pop((paths.camera, day), None)
        return {"id": obj_id, "value": body.value}

    @app.get("/api/days/{day}/{camera}/objects/{obj_id}/thumb")
    def object_thumb(day: str, camera: str, obj_id: str, size: int = 0):
        """The object's middle crop (PNG with transparency); size > 0 shrinks it to fit that many pixels
        and returns a JPEG on the report's light gray (so a PDF with hundreds of objects stays small)."""
        import pickle

        from fastapi.responses import Response

        if not library.DAY_RE.fullmatch(day) or not re.fullmatch(r"[0-9.]+_[a-z]+(_\d+)?", obj_id):
            raise HTTPException(400, "bad object id")
        tubes = DayPaths(_check_camera(camera), date.fromisoformat(day)).tubes
        f = tubes / f"{obj_id}.pkl"
        if not f.is_file():
            raise HTTPException(404, "no such object")
        # Object files hold every frame (up to tens of MB); keep the one picture the app shows next to
        # them, so lists and reports with hundreds of objects don't read gigabytes. A re-analysis
        # replaces the whole folder, cache included.
        size = max(0, min(size, 2000))
        cached = tubes / "_thumbs" / (f"{obj_id}_{size}.jpg" if size else f"{obj_id}.png")
        try:
            png = cached.read_bytes() if cached.stat().st_mtime >= f.stat().st_mtime else None
        except OSError:
            png = None
        if png is None:
            with open(f, "rb") as fh:
                frames = pickle.load(fh)["frames"]
            png = frames[len(frames) // 2][5]
            if size:
                import cv2
                import numpy as np

                img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_UNCHANGED)
                scale = min(1.0, size / max(img.shape[:2]))
                img = cv2.resize(img, (max(1, round(img.shape[1] * scale)), max(1, round(img.shape[0] * scale))),
                                 interpolation=cv2.INTER_AREA)
                a = img[:, :, 3:4].astype(np.float32) / 255
                paper = np.array([242, 244, 244], np.float32)  # #F4F4F2 in BGR, the report's cell color
                flat = (img[:, :, :3] * a + paper * (1 - a)).astype(np.uint8)
                png = cv2.imencode(".jpg", flat, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()
            try:
                cached.parent.mkdir(exist_ok=True)
                cached.write_bytes(png)
            except OSError:
                pass  # read-only location: serve it uncached
        return Response(png, media_type="image/jpeg" if size else "image/png", headers={"Cache-Control": "max-age=300"})

    # ---------------------------------------------------------------- jobs
    @app.get("/api/jobs")
    def jobs():
        return [j.summary() for j in sorted(queue.jobs.values(), key=lambda j: -j.id)]

    # Job-changing handlers are async so they run on the event loop with the scheduler.
    @app.post("/api/jobs", status_code=201)
    async def submit(req: JobRequest):
        """Queue work for a day. kind "run" (Everything) becomes three jobs; returns the first,
        with all of them under "jobs"."""
        cameras = req.cameras or settings_mod.load()["cameras"]
        for c in cameras:
            _check_camera(c)
        try:
            created = queue.submit(req.kind, _check_day(req.day), cameras, req.options)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {**created[0].summary(), "jobs": [j.summary() for j in created]}

    def _job(job_id: int):
        if job_id not in queue.jobs:
            raise HTTPException(404, "no such job")
        return queue.jobs[job_id]

    @app.get("/api/queue")
    def queue_state():
        return queue.queue_state()

    @app.post("/api/queue/pause")
    async def pause_queue():
        queue.pause("you paused it")
        return queue.queue_state()

    @app.post("/api/queue/resume")
    async def resume_queue():
        queue.resume()
        return queue.queue_state()

    @app.post("/api/jobs/{job_id}/retry")
    async def retry(job_id: int):
        _job(job_id)
        try:
            return queue.retry(job_id).summary()
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/jobs/{job_id}/move")
    async def move(job_id: int, delta: int):
        """Move a queued job up (delta=-1) or down (delta=1)."""
        _job(job_id)
        try:
            return queue.move(job_id, delta).summary()
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/jobs/{job_id}")
    def job(job_id: int):
        if job_id not in queue.jobs:
            raise HTTPException(404, "no such job")
        return queue.jobs[job_id].summary(with_log=True)

    @app.get("/api/jobs/{job_id}/log")
    def job_log(job_id: int):
        """The job's complete log as a downloadable text file."""
        if job_id not in queue.jobs:
            raise HTTPException(404, "no such job")
        job = queue.jobs[job_id]
        f = queue.log_path(job)
        if not f.exists():
            raise HTTPException(404, "no log yet")
        return FileResponse(f, media_type="text/plain; charset=utf-8",
                            filename=f"reolapse-job-{job.id}-{job.kind}-{job.day}.log")

    @app.delete("/api/jobs/{job_id}")
    async def remove_job(job_id: int):
        """Remove a finished job from the list (and its log)."""
        _job(job_id)
        try:
            return {"removed": queue.remove([job_id])}
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.delete("/api/jobs")
    async def clear_finished():
        """Remove every finished job (done, failed or cancelled)."""
        return {"removed": queue.remove()}

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel(job_id: int):
        if job_id not in queue.jobs:
            raise HTTPException(404, "no such job")
        return queue.cancel(job_id).summary()

    @app.get("/api/events")
    async def events():
        """Server-sent events: a snapshot of all jobs, then every job update as it happens."""
        q = queue.subscribe()

        async def stream():
            try:
                yield f"event: snapshot\ndata: {json.dumps([j.summary() for j in queue.jobs.values()])}\n\n"
                yield f"event: queue\ndata: {json.dumps(queue.queue_state())}\n\n"
                while True:
                    try:
                        event = await asyncio.wait_for(q.get(), timeout=15)
                        if "queue" in event:
                            yield f"event: queue\ndata: {json.dumps(event['queue'])}\n\n"
                        elif "removed" in event:
                            yield f"event: removed\ndata: {json.dumps(event['removed'])}\n\n"
                        else:
                            yield f"event: job\ndata: {json.dumps(event)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
            finally:
                queue.unsubscribe(q)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    # ---------------------------------------------------------------- settings / NVR
    @app.get("/api/settings")
    def get_settings():
        return settings_mod.load()

    @app.put("/api/settings")
    def put_settings(patch: dict):
        # The NVR login is saved only through PUT /api/nvr: the Settings page sends its whole form
        # here, and its copy of the login can be older than one saved a moment ago with Save login.
        patch = {k: v for k, v in patch.items() if k != "nvr"}
        try:
            saved = settings_mod.save(patch)
        except ValueError as e:
            raise HTTPException(400, str(e))
        config.DATA_ROOT = config._data_root()  # changed folders apply without a restart
        config.WORK_ROOT = config._work_root()
        library._cache.clear()
        library._day_list.clear()
        return saved

    @app.get("/api/models")
    def models():
        present = {p.name for p in config.MODEL_DIR.glob("*.pt")}
        known = {m["file"] for m in MODEL_CATALOG}
        extra = [{"file": f, "name": f, "speed": "", "about": "Custom model found in models/."}
                 for f in sorted(present - known)]
        return [{**m, "downloaded": m["file"] in present} for m in MODEL_CATALOG + extra]

    # ---------------------------------------------------------------- across days
    def _range(start: str, end: str, cameras: str) -> tuple[list[str], list[str]]:
        cams = [_check_camera(c) for c in cameras.split(",") if c] or settings_mod.load()["cameras"]
        lo, hi = sorted((_check_day(start), _check_day(end))) if start and end else ("0000-00-00", "9999-99-99")
        days = sorted(d for d in library._list_days(cams) if lo <= d <= hi)
        return days, cams

    def _reviewed_index(camera: str, day: str):
        paths = DayPaths(camera, date.fromisoformat(day))
        if not paths.tubes.is_dir():
            return None
        index = _tube_index(paths)
        return index and review.apply(index, review.load_corrections(paths.tubes), review.zones_for(camera))

    @app.get("/api/trends")
    async def trends(start: str = "", end: str = "", cameras: str = ""):
        """Per camera and day: counts and objects per hour by type; per saved report area: counts by day."""
        from .. import tube_index

        days, cams = _range(start, end, cameras)

        def collect():
            data = {c: {} for c in cams}
            for c in cams:
                for d in days:
                    info = library.camera_day(c, d)
                    if info["analyzed"]:
                        data[c][d] = {"counts": info["counts"], "hourly_by": info["hourly_by"], "status": info["status"]}
            areas = []
            for c, defined in settings_mod.load()["areas"].items():
                if c not in cams:
                    continue
                for a in defined:
                    per_day = {}
                    for d in days:
                        index = _reviewed_index(c, d) if d in data[c] else None
                        if index:
                            cnt = {"person": 0, "vehicle": 0, "animal": 0}
                            for o in tube_index.query(index, [(b["x0"], b["y0"], b["x1"], b["y1"]) for b in a["boxes"]]):
                                cnt[o["category"]] = cnt.get(o["category"], 0) + 1
                            per_day[d] = cnt
                    areas.append({"camera": c, "name": a["name"], "days": per_day})
            return {"days": days, "cameras": cams, "data": data, "areas": areas}

        return await asyncio.to_thread(collect)

    @app.get("/api/search")
    async def search(start: str = "", end: str = "", cameras: str = "", types: str = "", after: str = "",
                     before: str = "", area: str = "", min_seconds: float = 0, limit: int = 600):
        """Objects across days: by camera, type, time of day (after/before "HH:MM", may wrap past
        midnight), a saved report area ("camera|index"), and how long they were on camera."""
        from .. import tube_index

        days, cams = _range(start, end, cameras)
        cats = {t for t in types.split(",") if t} or None
        clock_s = lambda v: int(v[:2]) * 3600 + int(v[3:5]) * 60 if re.fullmatch(r"\d\d:\d\d", v or "") else None
        lo, hi = clock_s(after), clock_s(before)
        regions, area_name = None, ""
        if area:
            cam, _, i = area.rpartition("|")
            defined = settings_mod.load()["areas"].get(cam, [])
            if not i.isdigit() or int(i) >= len(defined):
                raise HTTPException(400, "no such area")
            cams, a = [cam], defined[int(i)]
            regions, area_name = [(b["x0"], b["y0"], b["x1"], b["y1"]) for b in a["boxes"]], a["name"]

        def in_window(t: float) -> bool:
            t = t % 86400
            if lo is None and hi is None:
                return True
            if lo is not None and hi is not None and lo > hi:  # e.g. 22:00 to 05:00
                return t >= lo or t < hi
            return (lo is None or t >= lo) and (hi is None or t < hi)

        def collect():
            found, total = [], 0
            for d in sorted(days, reverse=True):  # newest first
                for c in cams:
                    index = _reviewed_index(c, d)
                    if not index:
                        continue
                    for o in tube_index.query(index, regions or [(0, 0, 1, 1)], cats):
                        if in_window(o["enter"]) and o["t1"] - o["t0"] >= min_seconds:
                            total += 1
                            if len(found) < limit:
                                found.append({**o, "day": d, "camera": c})
            return {"total": total, "shown": len(found), "area": area_name, "objects": found}

        return await asyncio.to_thread(collect)

    # ---------------------------------------------------------------- nightly run & storage
    @app.get("/api/nightly")
    async def nightly(preview: bool = False):
        """When the nightly run happens and what it last did; preview=1 adds what it would queue now."""
        out = scheduler.status()
        if preview:
            out["would_queue"] = await asyncio.to_thread(scheduler.plan_days)
        return out

    @app.post("/api/nightly/run")
    async def nightly_run():
        """Do tonight's run now: queue the days that need work (and clean up, if that's on)."""
        return await scheduler.run_now()

    @app.get("/api/storage")
    async def storage(refresh: bool = False):
        """Free space on the archive and working folders, and what the cleanup would delete."""
        from .. import cleanup

        hit = storage_cache.get("v")
        if hit and not refresh and time.time() - hit["scanned"] < 600:
            return hit
        protected = {(config.slug(c), d) for c, d in scheduler.busy_days()}
        p = await asyncio.to_thread(cleanup.plan, settings_mod.load()["retention"], protected)
        v = {"scanned": time.time(), "archive": cleanup.disk(config.DATA_ROOT), "work": cleanup.disk(config.WORK_ROOT),
             "same": config.DATA_ROOT.resolve() == config.WORK_ROOT.resolve(), "would_free": p.bytes,
             "items": p.summary(), "last_cleanup": scheduler.state.get("last_cleanup")}
        storage_cache["v"] = v
        return v

    @app.get("/api/storage/sharing")
    async def sharing_pending():
        """Days this Mac analyzed whose results aren't on the share yet."""
        from .. import sync

        days = await asyncio.to_thread(sync.pending)
        return {"days": [{"camera": p.camera, "day": p.day.isoformat()} for p in days]}

    @app.post("/api/storage/share")
    async def share_now():
        """Copy those days' objects and backgrounds to the share (skips days with running jobs)."""
        from .. import sync

        busy = scheduler.busy_days()
        done = []
        for p in await asyncio.to_thread(sync.pending):
            if (p.camera, p.day.isoformat()) in busy:
                continue
            await asyncio.to_thread(sync.share_results, p, lambda *_: None)
            done.append({"camera": p.camera, "day": p.day.isoformat()})
        library._cache.clear()
        return {"shared": done}

    @app.put("/api/kept/{day}")
    def keep_day(day: str, body: KeepBody):
        """Mark a day to keep: the cleanup never deletes its footage or results."""
        kept = {**settings_mod.load().get("kept", {}), _check_day(day): (body.note or "").strip()[:200]}
        storage_cache.clear()
        return settings_mod.save({"kept": kept})["kept"]

    @app.delete("/api/kept/{day}")
    def release_day(day: str):
        kept = {d: n for d, n in settings_mod.load().get("kept", {}).items() if d != _check_day(day)}
        storage_cache.clear()
        return settings_mod.save({"kept": kept})["kept"]

    @app.post("/api/storage/cleanup")
    async def storage_cleanup():
        out = await scheduler.clean()
        storage_cache.clear()
        return out

    # ---------------------------------------------------------------- updates
    @app.get("/api/update")
    async def update_status(check: bool = False, force: bool = False):
        """Current/latest version; ?check=1 asks GitHub (at most hourly unless force=1)."""
        return await updater.check(force=force) if check else updater.status()

    @app.put("/api/update/token")
    async def update_token(body: TokenBody):
        from .updates import set_token

        set_token(body.token)
        return await updater.check(force=True) if body.token else updater.status()

    @app.post("/api/update/download")
    async def update_download():
        try:
            return updater.start_download()
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/nvr")
    def nvr_info():
        """Who we connect as (never the password); source is ".env" or "app"."""
        from .. import nvr as nvrmod

        c = nvrmod.credentials()
        return {"host": c["host"], "user": c["user"], "port": c["port"], "password_set": bool(c["password"]),
                "source": c["source"]}

    @app.put("/api/nvr")
    def nvr_save(body: NvrLogin):
        """Save the NVR address/login; the password goes to the system keychain."""
        from .. import nvr as nvrmod

        if not body.host.strip() or not body.user.strip():
            raise HTTPException(400, "address and username are required")
        if re.search(r"[&#%\s]", body.password or ""):
            raise HTTPException(400, "the NVR rejects passwords containing & # % or spaces in its web API")
        nvrmod.save_credentials(body.host, body.user, body.port, body.password or None)
        return nvr_info()

    @app.post("/api/nvr/test")
    async def nvr_test():
        from .. import nvr as nvrmod

        try:
            async with nvrmod.connect() as nvr:
                return {"ok": True, "model": nvr.model,
                        "channels": [{"channel": ch, "name": nvr.camera_name(ch), "model": nvr.camera_model(ch)}
                                     for ch in nvr.channels]}
        except (Exception, SystemExit) as e:
            return {"ok": False, "error": str(e)}

    if UI_DIR.is_dir():  # mounted last so /api routes win
        app.mount("/", UIFiles(directory=UI_DIR, html=True), name="ui")

    return app
