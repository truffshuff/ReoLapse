"""A one-at-a-time queue of pipeline jobs, each run as a `python -m reolapse` subprocess.

Jobs run sequentially because analysis owns the GPU. The subprocess is started with
REOLAPSE_PROGRESS=json, so the pipeline prints `@@{json}` progress lines alongside its
normal output; those update `job.progress`, everything else goes to `job.log`.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import re
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from .. import settings as settings_mod

KINDS = ("fetch", "analyze", "render", "run")
_ids = itertools.count(1)


@dataclass
class Job:
    kind: str
    day: str
    cameras: list[str]
    options: dict = field(default_factory=dict)
    id: int = field(default_factory=lambda: next(_ids))
    status: str = "queued"  # queued | running | done | failed | cancelled
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    returncode: int | None = None
    progress: dict = field(default_factory=dict)
    log: deque = field(default_factory=lambda: deque(maxlen=400))
    proc: asyncio.subprocess.Process | None = field(default=None, repr=False)

    def summary(self, with_log: bool = False) -> dict:
        d = {k: getattr(self, k) for k in ("id", "kind", "day", "cameras", "options", "status", "created",
                                           "started", "finished", "returncode", "progress")}
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
    def __init__(self, command_for: Callable[[Job], list[str]] = default_command):
        self.command_for = command_for
        self.jobs: dict[int, Job] = {}
        self._pending: asyncio.Queue[Job] = asyncio.Queue()
        self._listeners: set[asyncio.Queue] = set()
        self._worker: asyncio.Task | None = None

    # -- lifecycle
    def start(self) -> None:
        if self._worker is None:
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
        job = Job(kind, day, list(cameras), options or {})
        self.jobs[job.id] = job
        self._pending.put_nowait(job)
        self._publish(job)
        return job

    def cancel(self, job_id: int) -> Job:
        job = self.jobs[job_id]
        if job.status == "queued":
            job.status = "cancelled"
            job.finished = time.time()
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

    async def _run_forever(self) -> None:
        while True:
            job = await self._pending.get()
            if job.status == "cancelled":
                continue
            await self._run(job)

    async def _run(self, job: Job) -> None:
        job.status, job.started = "running", time.time()
        self._publish(job)
        env = {**os.environ, "REOLAPSE_PROGRESS": "json", "PYTHONUNBUFFERED": "1"}
        try:
            job.proc = await asyncio.create_subprocess_exec(
                *self.command_for(job), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env)
            await _keep_awake_while(job.proc.pid)
            buf = ""
            while chunk := await job.proc.stdout.read(4096):
                buf += chunk.decode(errors="replace")
                *lines, buf = re.split(r"[\r\n]", buf)
                for line in lines:
                    self._line(job, line)
            if buf:
                self._line(job, buf)
            job.returncode = await job.proc.wait()
        except Exception as e:  # spawn failure etc.
            job.log.append(f"error: {e}")
            job.returncode = -1
        job.finished = time.time()
        if job.status != "cancelled":
            job.status = "done" if job.returncode == 0 else "failed"
        job.proc = None
        self._publish(job)

    def _line(self, job: Job, line: str) -> None:
        line = line.rstrip()
        if not line:
            return
        if line.startswith("@@"):
            try:
                job.progress = json.loads(line[2:])
            except json.JSONDecodeError:
                job.log.append(line)
            self._publish(job)
            return
        job.log.append(line)
        self._publish(job, line)
