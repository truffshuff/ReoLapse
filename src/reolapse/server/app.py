"""HTTP API for the desktop app: library, jobs (with a live event stream), settings, media."""

from __future__ import annotations

import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import config
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
     "about": "Most accurate of the three; only worth it if small objects are being missed."},
    {"file": "yolo11n-seg.pt", "name": "YOLO11 nano", "speed": "fastest",
     "about": "Previous generation nano model; kept for comparison."},
]

# The desktop UI's production build (app.nosync/dist), hosted at / so the app and API share an origin.
UI_DIR = Path(os.environ.get("REOLAPSE_UI_DIR", Path(__file__).resolve().parents[3] / "app.nosync" / "dist"))
from . import library
from .jobs import JobQueue


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


def create_app(queue: JobQueue | None = None) -> FastAPI:
    queue = queue or JobQueue()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        queue.start()
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

    @app.get("/api/days/{day}/{camera}/out/{name}")
    def output(day: str, camera: str, name: str):
        f = library.output_file(_check_camera(camera), _check_day(day), name)
        if f is None:
            raise HTTPException(404, "no such output")
        return FileResponse(f)  # supports Range requests, so <video> can seek

    @app.get("/api/days/{day}/{camera}/bg/{slot}")
    def background(day: str, camera: str, slot: int):
        f = library.background_file(_check_camera(camera), _check_day(day), slot)
        if f is None:
            raise HTTPException(404, "no such background")
        return FileResponse(f, media_type="image/jpeg")

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
        return {"width": index["width"], "height": index["height"], "objects": tube_index.query(index, boxes, cats)}

    @app.get("/api/days/{day}/{camera}/objects/{obj_id}/thumb")
    def object_thumb(day: str, camera: str, obj_id: str):
        """The object's middle crop (PNG with transparency)."""
        import pickle

        from fastapi.responses import Response

        if not library.DAY_RE.fullmatch(day) or not re.fullmatch(r"[0-9.]+_[a-z]+(_\d+)?", obj_id):
            raise HTTPException(400, "bad object id")
        f = DayPaths(_check_camera(camera), date.fromisoformat(day)).tubes / f"{obj_id}.pkl"
        if not f.is_file():
            raise HTTPException(404, "no such object")
        with open(f, "rb") as fh:
            frames = pickle.load(fh)["frames"]
        return Response(frames[len(frames) // 2][5], media_type="image/png",
                        headers={"Cache-Control": "max-age=86400"})

    # ---------------------------------------------------------------- jobs
    @app.get("/api/jobs")
    def jobs():
        return [j.summary() for j in sorted(queue.jobs.values(), key=lambda j: -j.id)]

    @app.post("/api/jobs", status_code=201)
    def submit(req: JobRequest):
        cameras = req.cameras or settings_mod.load()["cameras"]
        for c in cameras:
            _check_camera(c)
        try:
            return queue.submit(req.kind, _check_day(req.day), cameras, req.options).summary()
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

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: int):
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
                while True:
                    try:
                        event = await asyncio.wait_for(q.get(), timeout=15)
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
        try:
            saved = settings_mod.save(patch)
        except ValueError as e:
            raise HTTPException(400, str(e))
        config.WORK_ROOT = config._work_root()  # a changed working folder applies without a restart
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

    @app.get("/api/nvr")
    def nvr_info():
        # Credentials live in .env; only say who we connect as.
        return {"host": os.environ.get("REOLINK_HOST"), "user": os.environ.get("REOLINK_USER"),
                "password_set": bool(os.environ.get("REOLINK_PASSWORD"))}

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
