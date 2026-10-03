"""A one-at-a-time queue of pipeline jobs, each run as a `python -m reolapse` subprocess.

Jobs run sequentially because analysis owns the GPU. The subprocess is started with
REOLAPSE_PROGRESS=json; its output is read as segments ending in "\\r" or "\\n":

- `@@{json}`            -> job.progress (the progress bar)
- text ending in "\\r"   -> job.status, an in-place line (download speed, frames rendered…)
- text ending in "\\n"   -> job.log, and the job's full log file

The queue is saved to disk, so jobs survive a server restart: queued jobs stay queued, and a
job that was running is resumed (each pipeline step skips work that's already finished).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from .. import settings as settings_mod

KINDS = ("fetch", "analyze", "render", "run")
KIND_LABEL = {"run": "Everything (download, analyze, render)", "fetch": "Download", "analyze": "Find moving objects",
              "render": "Render videos and stills"}
STATE_DIR = settings_mod.SETTINGS_PATH.parent
HISTORY = 50  # finished jobs kept in the list


def _dur(sec: float) -> str:
    sec = int(max(0, sec))
    return f"{sec // 3600}h {sec % 3600 // 60:02d}m" if sec >= 3600 else f"{sec // 60}m {sec % 60:02d}s"


@dataclass
class Job:
    kind: str
    day: str
    cameras: list[str]
    options: dict = field(default_factory=dict)
    id: int = 0
    status: str = "queued"  # queued | running | done | failed | cancelled
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    returncode: int | None = None
    progress: dict = field(default_factory=dict)
    status_line: str = ""
    log: deque = field(default_factory=lambda: deque(maxlen=400))
    proc: asyncio.subprocess.Process | None = field(default=None, repr=False)

    FIELDS = ("id", "kind", "day", "cameras", "options", "status", "created", "started", "finished",
              "returncode", "progress", "status_line")

    def summary(self, with_log: bool = False) -> dict:
        d = {k: getattr(self, k) for k in self.FIELDS}
        if with_log:
            d["log"] = list(self.log)
        return d


def pipeline_args(job: Job, cfg: dict) -> list[str]:
    """CLI arguments for a job: settings provide defaults, job.options override them."""
    args = [job.kind, "--day", job.day]
    for cam in job.cameras:
        args += ["--camera", cam]
    a = {**cfg["analysis"], **{k: v for k, v in job.options.items() if k in cfg["analysis"]}}
    r = {**cfg["render"], **{k: v for k, v in job.options.items() if k in cfg["render"]}}
    if job.kind in ("analyze", "run"):
        args += ["--fps", str(a["fps"]), "--types", str(a["types"]), "--model", str(a["model"]),
                 "--imgsz", str(a["imgsz"])]
        if job.options.get("skip_bg"):
            args.append("--skip-bg")
    if job.kind in ("render", "run"):
        styles = r["styles"] if isinstance(r["styles"], list) else [r["styles"]]
        args += ["--style", ",".join(styles), "--color", str(r["color"]), "--drift", str(r["drift"]),
                 "--speed", str(r["speed"]), "--density", str(r["density"]), "--max-tube", str(r["max_tube"])]
    return args


def default_command(job: Job) -> list[str]:
    return [sys.executable, "-m", "reolapse", *pipeline_args(job, settings_mod.load())]


async def _keep_awake_while(pid: int) -> None:
    """macOS: stop idle sleep until the job process exits (caffeinate exits with it)."""
    if sys.platform != "darwin":
        return
    try:
        await asyncio.create_subprocess_exec("caffeinate", "-i", "-w", str(pid),
                                             stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    except OSError:
        pass


class JobQueue:
    def __init__(self, command_for: Callable[[Job], list[str]] = default_command, state_dir: Path | None = None):
        self.command_for = command_for
        self.state_dir = state_dir or STATE_DIR
        self.jobs: dict[int, Job] = {}
        self._pending: asyncio.Queue[Job] = asyncio.Queue()
        self._listeners: set[asyncio.Queue] = set()
        self._worker: asyncio.Task | None = None
        self._next_id = 1

    # -- persistence
    @property
    def _state_file(self) -> Path:
        return self.state_dir / "jobs.json"

    def log_path(self, job: Job) -> Path:
        return self.state_dir / "logs" / f"job-{job.id}.log"

    def _save(self) -> None:
        keep = sorted(self.jobs.values(), key=lambda j: j.id)
        finished = [j for j in keep if j.status not in ("queued", "running")]
        drop = {j.id for j in finished[:-HISTORY]} if len(finished) > HISTORY else set()
        data = [j.summary() for j in keep if j.id not in drop]
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            tmp = self._state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(data))
            tmp.replace(self._state_file)
        except OSError:
            pass

    def _restore(self) -> None:
        try:
            saved = json.loads(self._state_file.read_text())
        except (OSError, ValueError):
            return
        for d in saved:
            job = Job(**{k: d[k] for k in Job.FIELDS if k in d})
            if job.status == "running":  # the server stopped mid-job: pick it up again
                job.status = "queued"
                self._write_log(job, f"--- The server restarted while this job was running; resuming. "
                                     f"Steps that already finished are skipped.")
            self.jobs[job.id] = job
            self._next_id = max(self._next_id, job.id + 1)
        for job in sorted(self.jobs.values(), key=lambda j: j.id):
            if job.status == "queued":
                self._pending.put_nowait(job)
        try:  # the in-memory log tail, so the app shows recent lines after a restart
            for job in self.jobs.values():
                f = self.log_path(job)
                if f.exists():
                    job.log.extend(f.read_text(errors="replace").splitlines()[-400:])
        except OSError:
            pass

    # -- lifecycle
    def start(self) -> None:
        if self._worker is None:
            self._restore()
            self._worker = asyncio.create_task(self._run_forever())

    async def stop(self) -> None:
        for job in self.jobs.values():
            if job.proc and job.proc.returncode is None:
                job.proc.terminate()
        if self._worker:
            self._worker.cancel()

    # -- API
    def submit(self, kind: str, day: str, cameras: list[str], options: dict | None = None) -> Job:
        if kind not in KINDS:
            raise ValueError(f"unknown job kind {kind!r}")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise ValueError("day must be YYYY-MM-DD")
        job = Job(kind, day, list(cameras), options or {}, id=self._next_id)
        self._next_id += 1
        self.jobs[job.id] = job
        self._pending.put_nowait(job)
        when = date.fromisoformat(day).strftime("%a %b %-d")
        self._write_log(job, f"Queued: {KIND_LABEL[kind]} for {when}; cameras: {', '.join(job.cameras)}")
        self._save()
        self._publish(job)
        return job

    def cancel(self, job_id: int) -> Job:
        job = self.jobs[job_id]
        if job.status == "queued":
            job.status = "cancelled"
            job.finished = time.time()
            self._write_log(job, "Cancelled before it started.")
            self._save()
            self._publish(job)
        elif job.status == "running" and job.proc:
            job.status = "cancelled"
            job.proc.terminate()
        return job

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._listeners.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._listeners.discard(q)

    # -- internals
    def _publish(self, job: Job, line: str | None = None) -> None:
        event = {"job": job.summary()}
        if line is not None:
            event["line"] = line
        for q in list(self._listeners):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # slow consumer: it will resync from the next snapshot

    def _write_log(self, job: Job, line: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        job.log.append(f"{stamp}  {line}")
        try:
            f = self.log_path(job)
            f.parent.mkdir(parents=True, exist_ok=True)
            with open(f, "a") as fh:
                fh.write(f"{time.strftime('%Y-%m-%d')} {stamp}  {line}\n")
        except OSError:
            pass
        self._publish(job, job.log[-1])

    async def _run_forever(self) -> None:
        while True:
            job = await self._pending.get()
            if job.status == "cancelled":
                continue
            await self._run(job)

    async def _run(self, job: Job) -> None:
        job.status, job.started, job.status_line = "running", time.time(), ""
        self._save()
        self._write_log(job, "Started.")
        env = {**os.environ, "REOLAPSE_PROGRESS": "json", "PYTHONUNBUFFERED": "1"}
        try:
            job.proc = await asyncio.create_subprocess_exec(
                *self.command_for(job), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env)
            await _keep_awake_while(job.proc.pid)
            buf = ""
            while chunk := await job.proc.stdout.read(4096):
                buf += chunk.decode(errors="replace")
                parts = re.split(r"([\r\n])", buf)
                buf = parts.pop()  # unterminated tail
                for text, end in zip(parts[0::2], parts[1::2]):
                    self._segment(job, text, end)
            if buf:
                self._segment(job, buf, "\n")
            job.returncode = await job.proc.wait()
        except Exception as e:  # spawn failure etc.
            self._write_log(job, f"Could not run the job: {e}")
            job.returncode = -1
        job.finished = time.time()
        job.status_line = ""
        took = _dur(job.finished - job.started)
        if job.status == "cancelled":
            self._write_log(job, f"Cancelled after {took}.")
        elif job.returncode == 0:
            job.status = "done"
            self._write_log(job, f"Finished in {took}.")
        else:
            job.status = "failed"
            self._write_log(job, f"Failed after {took} (exit code {job.returncode}). The lines above show where it stopped.")
        job.proc = None
        self._save()
        self._publish(job)

    def _segment(self, job: Job, text: str, end: str) -> None:
        text = text.rstrip()
        if not text:
            return
        if text.startswith("@@"):
            try:
                job.progress = json.loads(text[2:])
            except json.JSONDecodeError:
                self._write_log(job, text)
                return
            self._publish(job)
        elif end == "\r":  # in-place status: shown live, not logged
            job.status_line = text.strip()
            self._publish(job)
        else:
            job.status_line = ""
            self._write_log(job, text)
